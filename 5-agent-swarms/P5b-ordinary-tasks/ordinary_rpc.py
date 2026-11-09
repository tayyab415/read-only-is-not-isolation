#!/usr/bin/env python3
"""One ordinary diagnostic fixture and a hidden matched background workload."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import sys
import threading
import time

import background
import p5_gate

HERE = Path(__file__).resolve().parent
WRITE_LOCK = threading.Lock()


def emit(value):
    with WRITE_LOCK:
        print(json.dumps(value), flush=True)


def retain(path, value):
    with WRITE_LOCK:
        with path.open("a") as stream:
            stream.write(json.dumps(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def main():
    setup = json.loads(sys.stdin.readline())
    run_id, configuration = setup["run_id"], setup["configuration"]
    smoke = setup.get("scripted_smoke", False)
    active = setup["background_active"]
    if not isinstance(active, int) or isinstance(active, bool) or active not in (0, 1):
        raise ValueError("Invalid private background assignment")
    background.deployment.p2.old.check_id(run_id)
    if not run_id.startswith("p5b-s1-" if smoke else "p5b-m1-"):
        raise ValueError("Unapproved ordinary-task fixture identifier")
    p5_gate.sources() if smoke else p5_gate.check()
    folder = HERE / "data" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    clients, records, stopped = {}, [], threading.Event()
    actor_locks = {actor: threading.Lock() for actor in ("alice", "bob")}
    status = {"run_id": run_id, "complete": False, "scripted": smoke,
              "background_active": active, "source_freeze_sha256": p5_gate.sha(HERE / "SOURCES-FROZEN.json")}
    with (background.deployment.P2 / "collection.lock").open("a") as common:
        fcntl.flock(common, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            public = background.deployment.provision(run_id, configuration, smoke=smoke)
            (folder / "provision.json").write_text(json.dumps(public, indent=2))
            for actor in ("alice", "bob"):
                clients[actor] = background.Client(run_id, actor, public, active)
            emit({"ready": True, "public": public,
                  "routes": {actor: background.deployment.route(public, actor) for actor in clients}})

            def dispatch(job):
                actor = job["actor"]
                try:
                    if stopped.is_set():
                        raise RuntimeError("Ordinary fixture stopped")
                    if actor == "bob" and job["requests"] != background.jobs(clients[actor].route):
                        raise ValueError("Background command list differs from its fixed schedule")
                    with actor_locks[actor]:
                        result = clients[actor].batch(job["requests"])
                    row = {"id": job["id"], "actor": actor, "request": job,
                           "return_utc_ns": time.time_ns(), **result}
                    retain(folder / "actions.jsonl", row)
                    records.append(row)
                    if not row["complete"]:
                        stopped.set()
                    emit(row)
                except Exception as error:
                    stopped.set()
                    row = {"id": job["id"], "actor": actor, "request": job, "complete": False,
                           "failure_type": type(error).__name__}
                    retain(folder / "actions.jsonl", row)
                    records.append(row)
                    emit(row)

            with ThreadPoolExecutor(max_workers=2) as pool:
                seen, futures, closed = set(), [], False
                actor_counts = {"alice": 0, "bob": 0}
                for line in sys.stdin:
                    job = json.loads(line)
                    if job["op"] == "close":
                        closed = True
                        break
                    identifier = job.get("id")
                    if job["op"] != "batch" or job.get("actor") not in clients \
                            or not isinstance(identifier, int) or isinstance(identifier, bool) \
                            or identifier in seen or len(seen) >= 16:
                        raise ValueError("Invalid ordinary-task RPC")
                    seen.add(identifier)
                    actor_counts[job["actor"]] += 1
                    if actor_counts[job["actor"]] > (4 if job["actor"] == "bob" else 12):
                        raise ValueError("Ordinary-task actor limit exhausted")
                    futures.append(pool.submit(dispatch, job))
                for future in futures:
                    future.result()
                status["complete"] = closed and not stopped.is_set()
        except Exception as error:
            status.update(complete=False, failure_type=type(error).__name__)
            raise
        finally:
            for client in clients.values():
                try:
                    client.close()
                except Exception as error:
                    status.update(complete=False, worker_stop_failure=type(error).__name__)
            time.sleep(.10)
            if background.deployment.p2.old.state_path(run_id).exists():
                try:
                    captured = background.deployment.snapshot(run_id)
                    (folder / "server.json").write_text(json.dumps(captured["server"]))
                    (folder / "server-log-lines.txt").write_text(
                        "\n".join(sorted(set(captured["server_log_lines"]))) + "\n")
                except Exception as error:
                    status.update(complete=False, evidence_failure_type=type(error).__name__)
            try:
                cleaned = background.deployment.cleanup(run_id)
                (folder / "cleanup.json").write_text(json.dumps(cleaned, indent=2))
                status["cleanup_absence_proved"] = cleaned["absence_proved"]
                status["complete"] = status["complete"] and cleaned["absence_proved"]
            except Exception as error:
                status.update(complete=False, cleanup_failure_type=type(error).__name__)
            status["batches"] = len(records)
            status["http_requests"] = sum(len(row.get("records", [])) for row in records)
            status["file_sha256"] = {file.name: p5_gate.sha(file) for file in folder.iterdir() if file.is_file()}
            (folder / "sealed.json").write_text(json.dumps(status, indent=2))
            emit({"closed": True, "status": status})


if __name__ == "__main__":
    main()
