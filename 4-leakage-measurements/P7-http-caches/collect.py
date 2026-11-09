"""Sequential P7 calibration or fresh holdout, with public failure retention."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

import plan
from request_guard import RequestGuard
import service

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_result(row, status=404, digest=service.MISSING_SHA):
    if row["status"] != status or row["body_sha256"] != digest:
        raise RuntimeError("The expected fixture status or bytes differ")


def canaries(runtime, delay):
    records = runtime.public["canaries"]["records"]
    marker = lambda name: f"p7/{runtime.run_id}/canary/{name}"
    missing = f"/missing/{delay}/canary-cache"
    for i in range(2):
        row = runtime.request("alice", "GET", missing, marker(f"cache-{i}"))
        records.append(row)
        checked_result(row)
    source = service.old.docker_log(runtime.origin)
    counts = [sum(row.get("marker") == marker(f"cache-{i}") for row in source) for i in range(2)]
    expected = [1, 1] if runtime.arm == "disabled" else [1, 0]
    runtime.public["canaries"].update(negative_lookup_counts=counts, expected_lookup_counts=expected)
    if counts != expected:
        raise RuntimeError("Configured 404 caching does not match observed reuse")
    positive = "/present/canary-functions"
    etag = '"' + service.PAYLOAD_SHA + '"'
    for i, (method, headers) in enumerate([
            ("GET", {}), ("GET", {}), ("HEAD", {}),
            ("GET", {"If-None-Match": etag}), ("GET", {"Range": "bytes=0-1"}), ("GET", {})]):
        row = runtime.request("bob", method, positive, marker(f"function-{i}"), headers)
        records.append(row)
        if method == "HEAD":
            checked_result(row, 200, hashlib.sha256(b"").hexdigest())
        elif not headers:
            checked_result(row, 200, service.PAYLOAD_SHA)
        elif row["status"] not in ((200, 304) if "If-None-Match" in headers else (200, 206)):
            raise RuntimeError("Declared successful-object canary failed")
        elif row["status"] == 304:
            checked_result(row, 304, hashlib.sha256(b"").hexdigest())
        elif row["status"] == 206:
            checked_result(row, 206, hashlib.sha256(b"J ").hexdigest())
        else:
            checked_result(row, 200, service.PAYLOAD_SHA)
    records.append(runtime.rejected_origin_request(marker("unknown-origin"), f"/missing/{delay}/canary-denied"))
    return {"passed": True, "negative_lookup_counts": counts, "expected_lookup_counts": expected,
            "records": records, "scope": "Configured 404 reuse and bounded successful-object protocol behavior."}


def holdout_gate():
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    if frozen["source_freeze_sha256"] != sha(HERE / "SOURCES-FROZEN.json"):
        raise RuntimeError("The P7 models refer to different sources")
    for name, expected in frozen["calibration_sha256"].items():
        path = (HERE / name).resolve()
        if HERE.resolve() not in path.parents or sha(path) != expected:
            raise RuntimeError("Frozen P7 calibration changed")
    for name, expected in frozen["model_sha256"].items():
        path = (HERE / name).resolve()
        if HERE.resolve() not in path.parents or sha(path) != expected:
            raise RuntimeError("Frozen P7 decoder changed")
    return frozen


def cohort(product, delay, arm, split):
    service.check_sources()
    service.p2.check_gate()
    if split == "holdout":
        holdout_gate()
    schedule = plan.schedule(product, delay, arm, split)
    tag = "cal" if split == "calibration" else "hold"
    run_id = f"p7-g2-{product}-{delay}-{arm}-{tag}"
    data = HERE / "data" / ("g2-cal" if split == "calibration" else "g2-main")
    folder = data / f"{product}-{delay}-{arm}"
    if folder.exists():
        raise RuntimeError("Preserve previous P7 cohort evidence; do not reuse its run ID")
    folder.mkdir(parents=True)
    guard = RequestGuard(HERE / "data/g1-request-ledger.jsonl")
    runtime = service.Runtime(run_id, product, arm, folder, guard)
    seal = {"run_id": run_id, "product": product, "delay_ms": delay, "arm": arm, "split": split,
            "scheduled": len(schedule), "completed": 0, "complete": False,
            "source_freeze_sha256": sha(HERE / "SOURCES-FROZEN.json")}
    if split == "holdout":
        seal["model_freeze_sha256"] = sha(HERE / "FROZEN.json")
    (folder / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
    (folder / "requests.jsonl").touch(exist_ok=False)
    try:
        runtime.prepare()
        runtime.public["canaries"] = {"passed": False, "records": []}
        # All dispatches are also retained in the request ledger, including
        # partial setup. A failed canary stops before primary observations.
        runtime.public["canaries"] = canaries(runtime, delay)
        (folder / "provision.json").write_text(json.dumps(runtime.public, indent=2) + "\n")
        with (folder / "trials.jsonl").open("x") as stream:
            for row in schedule:
                started = time.time_ns()
                dispatch = runtime.request(row["sender"], "GET", row["path"],
                    plan.marker(run_id, row, row["sender"], "sender"), execute=row["active"])
                sender_end = dispatch["end_utc_ns"]
                if row["active"]:
                    sender = dispatch
                    checked_result(sender)
                else:
                    sender = None
                    if not dispatch.get("idle") or dispatch["status"] is not None:
                        raise RuntimeError("The idle sender unexpectedly issued HTTP")
                receiver = runtime.request(row["receiver"], "GET", row["path"],
                                           plan.marker(run_id, row, row["receiver"], "receiver"))
                observed = {**row, "sender_request": sender, "receiver_request": receiver,
                            "sender_dispatch": dispatch,
                            "controller_start_utc_ns": started, "sender_dispatch_end_utc_ns": sender_end,
                            "controller_end_utc_ns": time.time_ns()}
                stream.write(json.dumps(observed) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                seal["completed"] += 1
                checked_result(receiver)
                if sender_end > receiver["start_utc_ns"]:
                    raise RuntimeError("Receiver started before sender dispatch completed")
                print(json.dumps({"run_id": run_id, "trial_id": row["trial_id"], "completed": seal["completed"]}), flush=True)
        seal["complete"] = seal["completed"] == len(schedule)
        runtime.public["resource_after_live"] = runtime.resource_snapshot()
    except Exception as error:
        seal["failure_type"] = type(error).__name__
        raise
    finally:
        try:
            stopped = runtime.stop_cache_processes()
            (folder / "cache-stop.json").write_text(json.dumps(stopped, indent=2) + "\n")
            if not stopped["passed"]:
                seal["complete"] = False
            (folder / "server.json").write_text(json.dumps(runtime.capture(), indent=2) + "\n")
        except Exception as error:
            seal.update(complete=False, evidence_failure_type=type(error).__name__)
        finally:
            (folder / "provision.json").write_text(json.dumps(runtime.public, indent=2) + "\n")
            cleanup = runtime.close()
            (folder / "cleanup.json").write_text(json.dumps(cleanup, indent=2) + "\n")
            if not cleanup["absence_proved"]:
                seal["complete"] = False
            seal["cleanup_absence_proved"] = cleanup["absence_proved"]
            seal["request_ledger_sha256_at_seal"] = sha(HERE / "data/g1-request-ledger.jsonl") \
                if (HERE / "data/g1-request-ledger.jsonl").exists() else None
            if (HERE / "data/g1-request-ledger.jsonl").exists():
                (folder / "request-reservations.jsonl").write_bytes((HERE / "data/g1-request-ledger.jsonl").read_bytes())
            if seal["complete"]:
                names = [path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()]
                seal["sha256"] = {name: sha(folder / name) for name in sorted(names)}
            (folder / "sealed.json").write_text(json.dumps(seal, indent=2) + "\n")
    if not seal["complete"]:
        raise RuntimeError("P7 cohort is incomplete")
    return seal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("calibration", "holdout"))
    parser.add_argument("product", choices=plan.PRODUCTS)
    parser.add_argument("delay", type=int, choices=plan.DELAYS)
    parser.add_argument("arm", choices=(*plan.ARMS, "no-sender"))
    args = parser.parse_args()
    with (service.P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(cohort(args.product, args.delay, args.arm, args.split)), flush=True)


if __name__ == "__main__":
    main()
