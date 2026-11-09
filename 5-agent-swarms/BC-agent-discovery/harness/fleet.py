#!/usr/bin/env python3
"""Run a schedule of episodes over a pool of nodes.

  python3 harness/fleet.py --schedule tasks/schedule.json --nodes node/nodes.json \
      --out data --max-parallel 3 [--stop-at-utc ISO] [--only-blocks C1 B1] [--fake]

Rules: schedule order is kept (head-of-line blocking); a node never runs two
episodes at once; STOP file or --stop-at-utc stops new launches only; a launch is
refused when ledger total + projected cost of in-flight and new episodes exceeds the
cap; episodes with an episode.json are skipped (resume).
"""
import argparse
import collections
import importlib.util
import json
import signal
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from episode import resolve_actors, run_episode  # noqa: E402
from ledger import DEFAULT_CAP, Ledger  # noqa: E402
from models import FakeModel, HFClient, assert_no_token, load_token  # noqa: E402
from node import FakeNode, SSHNode  # noqa: E402

BC = HERE.parent
EPISODE_MAX_USD = 0.10  # per-episode projection for the launch guard (24 calls, long histories)
QUARANTINE_AFTER = 2    # consecutive infra_failure episodes on a node


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_utc(text):
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def episode_dir(out_dir, row):
    return Path(out_dir) / row["study"] / row["episode_id"]


def read_final(d):
    p = d / "episode.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


def move_aside(d, tag):
    target = d.with_name(f"{d.name}.{tag}-{time.time_ns()}")
    d.rename(target)
    return target


def make_assignment(row, names):
    actors = resolve_actors(row)
    if row["cell"]["infra"] == "shared":
        slots = {"A": (names[0], 1), "B": (names[0], 2)}
    else:
        slots = {"A": (names[0], 1), "B": (names[1], 1)}
    return {a: {**actors[a], "node": slots[a][0], "slot": slots[a][1]} for a in actors}


def nodes_needed(row):
    infra = row["cell"]["infra"]
    if infra not in ("shared", "independent"):
        raise ValueError(f"bad infra {infra!r} in {row['episode_id']}")
    return 1 if infra == "shared" else 2


def run_fleet(rows, nodes, out_dir, model_client, ledger, prompts_module, fixtures_module,
              max_parallel=2, stop_event=None, stop_at=None, episode_max_usd=EPISODE_MAX_USD,
              poll_s=0.1, retry_failed=False, only_blocks=None, checker=None, runner=run_episode):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_event = stop_event or threading.Event()
    progress_path = out_dir / "progress.jsonl"
    plock = threading.Lock()
    summary = {"launched": 0, "skipped": 0, "statuses": collections.Counter(),
               "stopped": None, "quarantined": [], "not_run": 0}

    def progress(event, row=None, **kw):
        rec = {"utc": datetime.now(timezone.utc).isoformat(), "event": event, **kw}
        if row:
            rec.update(episode_id=row["episode_id"], block=row.get("block"))
        with plock, progress_path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    only = {b for item in (only_blocks or []) for b in str(item).split(",") if b}
    pending = [r for r in rows if not only or str(r.get("block")) in only]
    free = collections.deque(sorted(nodes))
    consec = collections.Counter()
    quarantined = set()

    def quarantine(name, why):
        quarantined.add(name)
        summary["quarantined"].append(name)
        progress("node_quarantined", node=name, detail=why)

    for name in sorted(nodes):  # preflight
        try:
            if not nodes[name].status().get("vllm_ok"):
                free.remove(name)
                quarantine(name, "vllm not ok")
        except Exception as e:
            free.remove(name)
            quarantine(name, f"status failed: {e}"[:300])

    def work(row, names):
        try:
            return runner(row, make_assignment(row, names), nodes, model_client, ledger, out_dir,
                          prompts_module, fixtures_module, checker=checker)
        except Exception as e:
            return {"status": "infra_failure", "teardown_ok": None,
                    "errors": [f"{type(e).__name__}: {e}"[:500]]}

    def stop_reason():
        if stop_event.is_set():
            return "interrupted"
        if (out_dir / "STOP").exists():
            return "stop_file"
        if stop_at and datetime.now(timezone.utc) >= stop_at:
            return "stop_at_utc"
        return None

    inflight = {}  # future -> (row, names)

    def reap(done):
        for fut in done:
            row, names = inflight.pop(fut)
            res = fut.result()
            status = res.get("status")
            summary["statuses"][status] += 1
            progress("done", row, status=status, nodes=names, errors=res.get("errors"),
                     ledger_total_usd=ledger.total())
            for n in names:
                if res.get("teardown_ok") is not True and res.get("teardown_ok") is not None:
                    quarantine(n, "teardown failed")
                elif status == "infra_failure":
                    consec[n] += 1
                    if consec[n] >= QUARANTINE_AFTER:
                        quarantine(n, f"{consec[n]} consecutive infra failures")
                else:
                    consec[n] = 0
                if n not in quarantined:
                    free.append(n)

    stopped = None
    i = 0
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while i < len(pending) or inflight:
            if inflight:
                done, _ = wait(list(inflight), timeout=poll_s, return_when=FIRST_COMPLETED)
                reap(done)
            if i >= len(pending):
                continue
            row = pending[i]
            d = episode_dir(out_dir, row)
            final = read_final(d)
            if final is not None and not (retry_failed and final.get("status") != "complete"):
                summary["skipped"] += 1
                progress("skip", row, status=final.get("status"))
                i += 1
                continue
            stopped = stopped or stop_reason()
            if not stopped and ledger.total() + (len(inflight) + 1) * episode_max_usd > ledger.cap:
                stopped = "budget"
            if not stopped:
                need = nodes_needed(row)
                alive = len(nodes) - len(quarantined)
                if alive < need and not inflight:
                    stopped = "no_nodes"
            if stopped:
                if summary["stopped"] is None:
                    summary["stopped"] = stopped
                    progress("stop_launching", row, detail=stopped,
                             ledger_total_usd=ledger.total())
                summary["not_run"] = len(pending) - i
                i = len(pending)
                continue
            if len(inflight) >= max_parallel or len(free) < need:
                if not inflight:
                    time.sleep(poll_s)
                continue
            if d.exists():
                tag = "retry" if final is not None else "partial"
                progress("moved_aside", row, detail=str(move_aside(d, tag)))
            names = [free.popleft() for _ in range(need)]
            fut = pool.submit(work, row, names)
            inflight[fut] = (row, names)
            summary["launched"] += 1
            progress("launch", row, nodes=names, ledger_total_usd=ledger.total())
            i += 1
    summary["statuses"] = dict(summary["statuses"])
    progress("fleet_end", detail=json.dumps(summary))
    return summary


def load_rows(path):
    data = json.loads(Path(path).read_text())
    rows = data["episodes"] if isinstance(data, dict) else data
    for r in rows:
        for k in ("episode_id", "study", "cell"):
            if k not in r:
                raise ValueError(f"schedule row missing {k!r}: {r}")
        nodes_needed(r)
    ids = [r["episode_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate episode_id in schedule")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--nodes", required=True)
    ap.add_argument("--out", default="data")
    ap.add_argument("--max-parallel", type=int, default=2)
    ap.add_argument("--stop-at-utc")
    ap.add_argument("--only-blocks", nargs="+")
    ap.add_argument("--fake", action="store_true", help="FakeNode + FakeModel + stub prompts/fixtures; no network")
    ap.add_argument("--retry-failed", action="store_true", help="re-run episodes whose status is not complete")
    ap.add_argument("--ledger", help="default: ledger/hf-ledger.jsonl (out/fake-ledger.jsonl with --fake)")
    ap.add_argument("--cap", type=float, default=DEFAULT_CAP)
    ap.add_argument("--episode-max-usd", type=float, default=EPISODE_MAX_USD)
    ap.add_argument("--prompts", default=str(BC / "tasks" / "prompts.py"))
    ap.add_argument("--fixtures", default=str(BC / "tasks" / "make_fixtures.py"))
    args = ap.parse_args(argv)

    rows = load_rows(args.schedule)
    node_specs = json.loads(Path(args.nodes).read_text())
    out = Path(args.out)
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: (print("interrupt: finishing in-flight episodes", flush=True),
                                             stop_event.set()))
    token = None
    if args.fake:
        tests = HERE / "tests"
        prompts = load_module(tests / "stub_prompts.py", "stub_prompts")
        fixtures = load_module(tests / "stub_fixtures.py", "stub_fixtures")
        nodes = {s["name"]: FakeNode(s["name"]) for s in node_specs}
        ledger = Ledger(args.ledger or out / "fake-ledger.jsonl", args.cap)
        model = FakeModel([[{"name": "bash", "arguments": {"command": "echo hi > out.txt"}},],
                           [{"name": "finish", "arguments": {"summary": "done"}}]], ledger)
    else:
        prompts = load_module(args.prompts, "bc_prompts")
        fixtures = load_module(args.fixtures, "bc_fixtures")
        nodes = {s["name"]: SSHNode(s["ssh"]) for s in node_specs}
        ledger = Ledger(args.ledger or BC / "ledger" / "hf-ledger.jsonl", args.cap)
        token = load_token()
        model = HFClient(token, ledger)

    summary = run_fleet(rows, nodes, out, model, ledger, prompts, fixtures,
                        max_parallel=args.max_parallel, stop_event=stop_event,
                        stop_at=parse_utc(args.stop_at_utc) if args.stop_at_utc else None,
                        episode_max_usd=args.episode_max_usd, retry_failed=args.retry_failed,
                        only_blocks=args.only_blocks)
    print(json.dumps(summary, indent=2))
    print(f"ledger total: ${ledger.total():.4f} of ${ledger.cap:.2f}")
    if token:
        assert_no_token(out, token)
        print("token scan of output directory: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
