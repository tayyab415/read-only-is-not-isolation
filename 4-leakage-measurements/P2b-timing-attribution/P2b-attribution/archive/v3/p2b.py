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
import select
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
sys.path.insert(0, str(P2))
import harness  # noqa: E402
import plan  # noqa: E402
import service  # noqa: E402
import preflight  # noqa: E402

old = service.old
ARMS = ("shared", "pergw", "pad", "aa")
SIZES = {"smoke": 8, "calibration": 100, "holdout": 300}
SEED = 2026110911
PAD_MS = 12
PROBE = "import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),1).close()"
SOURCE_NAMES = {
    "PROTOCOL.md", "PREDICTIONS.md", "p2b.py", "analyze.py", "audit.py",
    "gateway_pad.py", "sequence.py", "test_readiness.py",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_sources():
    entries = [line.split() for line in (HERE / "SOURCES-SHA256.txt").read_text().splitlines()]
    if len(entries) != len(SOURCE_NAMES) or {name for _, name in entries} != SOURCE_NAMES:
        raise RuntimeError("P2b source manifest is incomplete or duplicated")
    for expected, name in entries:
        if sha(HERE / name) != expected:
            raise RuntimeError(f"P2b frozen source changed: {name}")
    service.check_sources()


def p2_gate():
    """Verify the lead's decision against hashed, complete P2 evidence."""
    gate_path = HERE / "P2-GATE.json"
    if not gate_path.exists():
        raise SystemExit("P2-GATE.json is absent; the lead has not confirmed the P2 audit and null")
    gate = json.loads(gate_path.read_text())
    if not (gate.get("p2_holdout_audit_passed") is True and gate.get("p2_aa_null_passed") is True):
        raise SystemExit("P2 gate does not pass")
    expected_names = {"holdout-audit-results.json", "holdout-results.json", "FROZEN.json"}
    expected_names.update(f"holdout-seal-{arm}.json" for arm in plan.CONFIGURATIONS)
    hashes = gate.get("evidence_sha256", {})
    if set(hashes) != expected_names:
        raise RuntimeError("P2 gate omits its actual audit, results, model freeze, or seals")
    for name, expected in hashes.items():
        if sha(HERE / "gate-evidence" / name) != expected:
            raise RuntimeError(f"P2 gate evidence changed: {name}")
    if gate.get("p2_source_freeze_sha256") != sha(P2 / "SOURCES-FROZEN.json"):
        raise RuntimeError("P2 gate refers to another scientific source")
    evidence = HERE / "gate-evidence"
    audit = json.loads((evidence / "holdout-audit-results.json").read_text())
    results = json.loads((evidence / "holdout-results.json").read_text())
    runs = audit.get("runs", [])
    if audit.get("passed") is not True or audit.get("smoke") is not False \
            or audit.get("source_sha256") != sha(P2 / "audit.py") \
            or len(runs) != 4 or {r.get("configuration") for r in runs} != set(plan.CONFIGURATIONS) \
            or any(r.get("passed") is not True or r.get("trials") != 1000
                   or r.get("split") != "holdout" for r in runs):
        raise RuntimeError("P2 gate contradicts its complete native audit")
    if results.get("aa_nominal_pass") is not True \
            or set(results.get("results", {})) != set(plan.CONFIGURATIONS) \
            or any(r.get("trials") != 1000 for r in results["results"].values()):
        raise RuntimeError("P2 gate contradicts its held results or A/A null")
    if sha(evidence / "FROZEN.json") != sha(P2 / "FROZEN.json"):
        raise RuntimeError("P2 gate model freeze differs from the deployed freeze")
    for arm in plan.CONFIGURATIONS:
        seal_path = evidence / f"holdout-seal-{arm}.json"
        remote_seal = P2 / "data/p2-m1" / arm / "holdout-sealed.json"
        seal = json.loads(seal_path.read_text())
        if sha(seal_path) != sha(remote_seal) or seal.get("complete") is not True \
                or seal.get("trials") != 1000 or seal.get("cleanup_absence_proved") is not True:
            raise RuntimeError("P2 gate does not match a complete, cleaned-up holdout")
        folder = remote_seal.parent
        inputs = {**seal["evidence_sha256"],
                  "holdout.jsonl": seal["sha256"],
                  "holdout-provision.json": seal["provision_sha256"]}
        if any(sha(folder / name) != expected for name, expected in inputs.items()):
            raise RuntimeError("P2 held evidence changed after its seal")
    return gate


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


def network_control_jobs(state):
    ips = {a: old.ip_address(state["containers"][a], state["networks"][a]) for a in ("alice", "bob")}
    backend = old.ip_address("art-a", state["networks"]["services"])
    jobs = {
        "bob": [{"host": ips["bob"], "port": 18888, "expected_connected": True,
                 "name": "live peer listener on its own network"}],
        "alice": [{"host": ips["bob"], "port": 18888, "expected_connected": False,
                   "name": "same live listener from the other network"}],
    }
    for actor in ("alice", "bob"):
        jobs[actor].extend([
            {"host": state["p2b_hosts"][actor], "port": 8000, "expected_connected": True,
             "name": "assigned gateway route"},
            {"host": backend, "port": 8082, "expected_connected": False,
             "name": "direct backend bypass"},
        ])
        if state["p2b_arm"] == "pergw":
            peer = "bob" if actor == "alice" else "alice"
            jobs[actor].append({
                "host": old.ip_address(state["p2b_hosts"][peer], state["networks"][peer]),
                "port": 8000, "expected_connected": False, "name": "peer gateway route",
            })
    for role in ("gateway", "gateway_bob") if state["p2b_arm"] == "pergw" else ("gateway",):
        jobs[role] = [{"host": backend, "port": 8082, "expected_connected": True,
                       "name": "backend is reachable from the gateway"}]
    return jobs


def network_checks(state):
    listener = subprocess.Popen(
        ["docker", "exec", "-i", state["containers"]["bob"], "python3", "-u", "-c", preflight.LISTENER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = {"passed": False, "listener_stopped": False, "records": []}
    try:
        if not select.select([listener.stdout], [], [], 5)[0] \
                or not json.loads(listener.stdout.readline()).get("ready"):
            raise RuntimeError("P2b live listener positive control failed")
        for role, jobs in network_control_jobs(state).items():
            result = old.run(["docker", "exec", "-i", state["containers"][role],
                              "python3", "-c", preflight.PROBE], input=json.dumps(jobs), timeout=12)
            if result.returncode:
                raise RuntimeError("P2b controlled network probe failed")
            rows = json.loads(result.stdout)
            if len(rows) != len(jobs) or any(any(row.get(k) != v for k, v in job.items())
                                          for row, job in zip(rows, jobs)):
                raise RuntimeError("P2b network probe differs from its owned target")
            output["records"].extend({**row, "actor": role} for row in rows)
        output.update(preflight.check_results(output["records"]))
        return output
    finally:
        try:
            listener.stdin.write("stop\n")
            listener.stdin.flush()
        except BrokenPipeError:
            pass
        listener.stdin.close()
        try:
            listener.wait(timeout=3)
            output["listener_exit_code"] = listener.returncode
            output["listener_stopped"] = listener.returncode == 0
            if listener.returncode:
                raise RuntimeError("P2b listener stop failed")
        except subprocess.TimeoutExpired:
            listener.kill()
            listener.wait(timeout=3)
            output["listener_exit_code"] = listener.returncode
            raise RuntimeError("P2b listener stop was not proved")
        finally:
            listener.stdout.close()
            listener.stderr.close()


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
                  network_positive_controls=network_checks(state),
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
            "receiver_schedule_lead_ms": min((epoch - r["schedule_setup_utc_ns"]) / 1e6 for r in received),
            "sender_overrun_ms": max(0, max((r["end_utc_ns"] for r in sent), default=epoch)
                                      - epoch - 2_000_000_000) / 1e6,
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
        import analyze
        analyze.verify_freeze(HERE / "data")
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
        if not cleanup["absence_proved"]:
            status["complete"] = False
        if status["complete"] and cleanup["absence_proved"]:
            status.update(sha256=sha(output), trials=records, sealed_utc_ns=time.time_ns(),
                          source_manifest_sha256=sha(HERE / "SOURCES-SHA256.txt"),
                          evidence_sha256={f"{split}-{suffix}": sha(folder / f"{split}-{suffix}")
                              for suffix in ("provision.json", "schedule.json", "server.json",
                                             "server-log-lines.txt", "cleanup.json")})
        (folder / f"{split}-sealed.json").write_text(json.dumps(status, indent=2))
    if not status["complete"]:
        raise RuntimeError("Collection incomplete")
    return status


def main():
    split = sys.argv[1]
    if split not in SIZES:
        raise SystemExit(__doc__)
    check_sources()
    p2_finished()
    p2_gate()
    with (P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for arm in ARMS:
            print(json.dumps(execute(arm, split)), flush=True)


if __name__ == "__main__":
    main()
