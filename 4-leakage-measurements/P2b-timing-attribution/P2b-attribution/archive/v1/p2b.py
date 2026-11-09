#!/usr/bin/env python3
"""P2b collection. Runs on the labhost from next/P2b-attribution, next to next/P2-residual.

    python3 p2b.py smoke         # 8 trials per arm
    python3 p2b.py calibration   # 100 trials per arm
    python3 p2b.py holdout       # 300 trials per arm, fresh fixtures; needs DECODERS-FROZEN.json

Reuses P2's worker, harness, plan and service modules and P1's provisioning.
Owned fixtures only. No analysis, no model calls.
"""
import fcntl
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
sys.path.insert(0, str(P2))
import harness  # noqa: E402
import plan  # noqa: E402
import service  # noqa: E402

old = service.old
ARMS = ("shared", "pergw", "pad", "aa")
SIZES = {"smoke": 8, "calibration": 100, "holdout": 300}
SEED = 2026110911
PAD_MS = 12
PROBE = "import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),1).close()"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def p2_finished():
    for configuration in ("separate", "sham", "aa", "immutable"):
        sealed = P2 / "data/p2-m1" / configuration / "holdout-sealed.json"
        if not sealed.exists() or not json.loads(sealed.read_text()).get("complete"):
            raise SystemExit(f"P2 holdout for {configuration} is not sealed; refusing to share the labhost")
    if any(name.startswith("p1-p2-") for name in old.run_ok(
            ["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()):
        raise SystemExit("P2 runtime containers still exist")


def schedule(arm, split):
    n = SIZES[split]
    rng = random.Random(f"{SEED}:{arm}:{split}")
    rows = []
    for sender in ("alice", "bob"):
        for label in (0, 1):
            for _ in range(n // 4):
                rows.append({"label": label, "recipe": "get", "sender": sender,
                             "receiver": "bob" if sender == "alice" else "alice",
                             "token": f"{rng.getrandbits(64):016x}"})
    rng.shuffle(rows)
    for index, row in enumerate(rows):
        row.update(index=index, split=split, trial_id=f"{split[0]}{index:04d}")
    return rows


def start_gateway(name, networks, config, script):
    path = HERE / "runtime-config" / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(config))
    old.run(["docker", "rm", "-f", name])
    old.sandbox(name, networks[0], extra=("-v", f"{script}:/gateway.py:ro", "-v", f"{path}:/gateway.json:ro"),
                command=("python3", "/gateway.py"))
    for network in networks[1:]:
        old.run_ok(["docker", "network", "connect", network, name])


def provision(run_id, arm):
    if old.free_gib() < 2:
        raise RuntimeError("Less than 2 GiB memory is available")
    public = old.provision(run_id, "separate")
    state = old.load_state(run_id)
    base = json.loads((old.ROOT / f"gateway-{run_id}.json").read_text())
    base["queue_size"] = 5
    nets, cont = state["networks"], state["containers"]
    checks = {}
    if arm in ("shared", "aa", "pad"):
        cfg = dict(base)
        script = P2 / "gateway.py"
        if arm == "pad":
            cfg["min_response_ms"] = PAD_MS
            script = HERE / "gateway_pad.py"
        start_gateway(cont["gateway"], [nets["alice"], nets["bob"], nets["services"]], cfg, script)
        for actor in ("alice", "bob"):
            old.wait_ready(cont[actor], cont["gateway"])
        hosts = {"alice": cont["gateway"], "bob": cont["gateway"]}
    else:
        ip = {a: old.ip_address(cont[a], nets[a]) for a in ("alice", "bob")}
        hosts = {"alice": cont["gateway"], "bob": f"p1-{run_id}-gateway-bob"}
        for actor in ("alice", "bob"):
            repo = state["repositories"][actor]
            cfg = {**base, "origin_binding": {ip[actor]: base["origin_binding"][ip[actor]]},
                   "repos": {repo: base["repos"][repo]}}
            start_gateway(hosts[actor], [nets[actor], nets["services"]], cfg, P2 / "gateway.py")
            old.wait_ready(cont[actor], hosts[actor])
        state["containers"]["gateway_bob"] = hosts["bob"]
        # Negative controls: neither client can reach the other's gateway.
        for actor, other in (("alice", "bob"), ("bob", "alice")):
            code = old.run(["docker", "exec", cont[actor], "python3", "-c", PROBE, hosts[other], "8000"]).returncode
            checks[f"{actor}_cannot_reach_{other}_gateway"] = code != 0
        if not all(checks.values()):
            raise RuntimeError("A client can reach the other tenant's gateway")
    state["p2b_hosts"], state["p2b_arm"] = hosts, arm
    old.state_path(run_id).write_text(json.dumps(state))
    public.update(arm=arm, hosts=hosts, pad_ms=PAD_MS if arm == "pad" else 0, gateway_queue_size=5,
                  negative_route_checks=checks, boundary_readback=service.boundary_readback(run_id),
                  gateway_sources={"p2_gateway": sha(P2 / "gateway.py"), "pad_gateway": sha(HERE / "gateway_pad.py")})
    return public


def client(run_id, actor, state):
    private = old.load_credentials(run_id)
    command = ["docker", "exec", "-i", state["containers"][actor], "python3", "-u", "-c",
               (P2 / "worker.py").read_text()]
    config = {"host": state["p2b_hosts"][actor], "port": 8000, "workers": 8,
              "user": state["users"][actor], "password": private[state["users"][actor]]}
    return harness.Client(command, config, actor, state)


def run_trial(state, run_id, arm, trial, clients):
    started = time.time_ns()
    sender, receiver = clients[trial["sender"]], clients[trial["receiver"]]
    send_jobs = plan.sender_jobs(state, run_id, {**trial, "label": 1}, "separate")
    recv_jobs = plan.receiver_jobs(state, run_id, trial)
    epoch = time.time_ns() + 300_000_000
    # Same command list every trial; the private flag inside the sender decides execution.
    sender.start(send_jobs, epoch, execute_http=int(trial["label"] == 1 and arm != "aa"))
    receiver.start(recv_jobs, epoch)
    sent, received = sender.finish(), receiver.finish()
    failed = [row["marker"] for row in sent + received if row.get("status") is None or row.get("status", 0) >= 500]
    return {**trial, "run_id": run_id, "arm": arm, "epoch_utc_ns": epoch, "sender_records": sent,
            "post": received, "controller_start_utc_ns": started, "controller_end_utc_ns": time.time_ns(),
            "failed_request_markers": failed}


def execute(arm, split):
    run_id = f"p2b-{split[0]}-{arm}"
    old.check_id(run_id)
    folder = HERE / "data" / arm
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{split}.jsonl"
    if output.exists():
        raise RuntimeError(f"{output} already exists")
    if split == "holdout":
        frozen = json.loads((HERE / "DECODERS-FROZEN.json").read_text())
        if frozen["sha256"]["analyze.py"] != sha(HERE / "analyze.py"):
            raise RuntimeError("analyze.py changed after the decoder freeze")
    rows = schedule(arm, split)
    (folder / f"{split}-schedule.json").write_text(json.dumps(rows))
    clients, lines, records, status = {}, set(), 0, {"run_id": run_id, "arm": arm, "split": split, "complete": False}
    try:
        public = provision(run_id, arm)
        (folder / f"{split}-provision.json").write_text(json.dumps(public, indent=2))
        state = old.load_state(run_id)
        clients = {actor: client(run_id, actor, state) for actor in ("alice", "bob")}
        with output.open("x") as stream:
            for trial in rows:
                record = run_trial(state, run_id, arm, trial, clients)
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                records += 1
                print(json.dumps({"run_id": run_id, "trial": trial["trial_id"], "retained": records}), flush=True)
                if record["failed_request_markers"]:
                    raise RuntimeError("A request failed; stopping the batch")
                if records % 50 == 0:
                    lines.update(service.server_log_lines(run_id))
                time.sleep(0.5)
        status["complete"] = records == len(rows)
    except Exception as error:
        status["failure_type"] = type(error).__name__
        raise
    finally:
        for c in clients.values():
            try:
                c.close()
            except Exception:
                status["complete"] = False
        if old.state_path(run_id).exists():
            try:
                server, recent = service.collect(run_id)
                lines.update(recent)
                if arm == "pergw":
                    server["gateway_bob_log"] = old.docker_log(f"p1-{run_id}-gateway-bob")
                (folder / f"{split}-server.json").write_text(json.dumps(server))
                (folder / f"{split}-server-log-lines.txt").write_text("\n".join(sorted(lines)) + "\n")
            except Exception as error:
                status.update(complete=False, evidence_failure_type=type(error).__name__)
        old.run(["docker", "rm", "-f", f"p1-{run_id}-gateway-bob"])
        cleanup = service.cleanup(run_id)
        (folder / f"{split}-cleanup.json").write_text(json.dumps(cleanup, indent=2))
        status.update(retained=records, cleanup_absence_proved=cleanup["absence_proved"])
        if status["complete"] and cleanup["absence_proved"]:
            status.update(sha256=sha(output), trials=records, sealed_utc_ns=time.time_ns())
        (folder / f"{split}-sealed.json").write_text(json.dumps(status, indent=2))
    if not status["complete"]:
        raise RuntimeError("Collection incomplete")
    return status


def main():
    split = sys.argv[1]
    if split not in SIZES:
        raise SystemExit(__doc__)
    p2_finished()
    with (P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for arm in ARMS:
            print(json.dumps(execute(arm, split)), flush=True)


if __name__ == "__main__":
    main()
