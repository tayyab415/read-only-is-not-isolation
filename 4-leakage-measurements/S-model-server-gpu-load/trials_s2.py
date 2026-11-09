#!/usr/bin/env python3
"""S2: does a gateway response-time floor remove the residual load channel?

Runs after S1 on the same server (prefix caching on, salted gateways). Reuses S1's text
generator, schedule and threshold fitting by import; S1 files are unchanged.
In every arm, b = 1 means tenant A starts a 2,400-word request; b = 0 means it sends nothing.
10 ms later tenant B sends a fresh 400-word prompt and times it.
  floor500_short   salted + 500 ms floor (port 9004), receiver max_tokens 1
  floor500_long    salted + 500 ms floor (port 9004), receiver 64 output tokens (ignore_eos)
  nofloor_long     salted, no floor (port 9002),      receiver 64 output tokens (ignore_eos)
Usage: trials_s2.py run --split calibration|holdout | freeze | utility
"""
import argparse, hashlib, json, sys, threading, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import trials as T  # noqa: E402

PHASE = "floor"
ARMS = {
    "floor500_short": {"port": 9004, "rx_tokens": 1},
    "floor500_long":  {"port": 9004, "rx_tokens": 64},
    "nofloor_long":   {"port": 9002, "rx_tokens": 64},
}
T.N = {"calibration": 80, "holdout": 240}
T.PHASES[PHASE] = {a: {"port": c["port"], "mode": "load"} for a, c in ARMS.items()}


def chat2(client, tenant, text, trial, role, max_tokens):
    body = {"model": T.MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0, "ignore_eos": True}
    t0 = time.perf_counter_ns()
    r = client.post("/v1/chat/completions", json=body,
                    headers={"authorization": f"Bearer {T.KEY_OF[tenant]}", "x-trial-id": trial, "x-role": role})
    t1 = time.perf_counter_ns()
    return {"status": r.status_code, "ms": (t1 - t0) / 1e6, "wall_ns": time.time_ns()}


def one(split, arm, i, bit, clients):
    cfg = ARMS[arm]
    ca, cb = clients[cfg["port"]]
    trial = f"{PHASE}-{split}-{arm}-{i:04d}"
    rec = {"phase": PHASE, "split": split, "arm": arm, "i": i, "bit": bit, "trial": trial}
    holder, th = {}, None
    if bit:
        heavy = T.words(f"{trial}-H", T.HEAVY_WORDS)
        th = threading.Thread(target=lambda: holder.update(s=chat2(ca, "tenant-a", heavy, trial, "sender", 1)))
        th.start()
    time.sleep(T.LOAD_LEAD_S)
    rec["receiver"] = chat2(cb, "tenant-b", T.words(f"{trial}-R", T.PREFIX_WORDS), trial, "receiver", cfg["rx_tokens"])
    if th:
        th.join()
    rec["sender"] = holder.get("s")
    return rec


def run(split):
    out = T.DATA / f"trials-{PHASE}-{split}.jsonl"
    assert not out.exists(), f"{out} exists; a split is collected once"
    if split == "holdout":
        assert (T.DATA / f"thresholds-{PHASE}.json").exists(), "freeze thresholds before holdout"
    clients = {p: (httpx.Client(base_url=f"http://127.0.0.1:{p}", timeout=120),
                   httpx.Client(base_url=f"http://127.0.0.1:{p}", timeout=120)) for p in {9002, 9004}}
    for p, (ca, cb) in clients.items():
        for i in range(10):
            T.chat(ca, "tenant-a", T.words(f"warm2-a-{p}-{i}", 50), f"warm2-{p}-{i}", "warmup")
            T.chat(cb, "tenant-b", T.words(f"warm2-b-{p}-{i}", 50), f"warm2-{p}-{i}", "warmup")
    sched = T.schedule(PHASE, split)
    with out.open("a") as f:
        for arm, i, bit in sched:
            f.write(json.dumps(one(split, arm, i, bit, clients)) + "\n")
            f.flush()
            time.sleep(T.INTER_TRIAL_S)
    print("wrote", out, len(sched))


def freeze():
    rows = [json.loads(l) for l in (T.DATA / f"trials-{PHASE}-calibration.jsonl").read_text().splitlines()]
    th = {}
    for arm in ARMS:
        rs = [r for r in rows if r["arm"] == arm and r["receiver"]["status"] == 200]
        t, acc = T.fit_threshold([r["receiver"]["ms"] for r in rs], [r["bit"] for r in rs], low_means_one=False)
        th[arm] = {"threshold_ms": t, "low_means_one": False, "calibration_accuracy": acc, "n": len(rs)}
    src = T.DATA / f"trials-{PHASE}-calibration.jsonl"
    payload = {"phase": PHASE, "thresholds": th, "frozen_utc_ns": time.time_ns(),
               "calibration_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
               "trials_s2_py_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (T.DATA / f"thresholds-{PHASE}.json").write_text(json.dumps(payload, indent=1))
    print(json.dumps(payload, indent=1))


def utility():
    """Latency of ordinary single requests with and without the floor, idle server."""
    rows = []
    for port, label in ((9002, "nofloor"), (9004, "floor500")):
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120)
        for kind, n_tok in (("probe_1tok", 1), ("turn_32tok", 32), ("turn_128tok", 128)):
            for i in range(30):
                r = chat2(c, "tenant-a", T.words(f"util2-{label}-{kind}-{i}", T.PREFIX_WORDS), f"util2-{label}-{kind}",
                          "utility", n_tok)
                rows.append({"gateway": label, "kind": kind, "i": i, **r})
    (T.DATA / "utility-floor.json").write_text(json.dumps(rows))
    print("wrote utility-floor.json", len(rows))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "freeze", "utility"])
    ap.add_argument("--split", choices=["calibration", "holdout"])
    a = ap.parse_args()
    {"run": lambda: run(a.split), "freeze": freeze, "utility": utility}[a.cmd]()
