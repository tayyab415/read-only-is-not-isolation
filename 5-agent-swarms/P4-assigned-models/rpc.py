#!/usr/bin/env python3
"""One quiet-window subject fixture with independent actor RPCs."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import sys
import threading
import time

import deployment
import gate

HERE = Path(__file__).resolve().parent
WRITE_LOCK = threading.Lock()


def emit(row):
    with WRITE_LOCK:
        print(json.dumps(row), flush=True)


def append(path, row):
    with WRITE_LOCK:
        with path.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())


def main():
    command = json.loads(sys.stdin.readline())
    run_id, configuration = command["run_id"], command["configuration"]
    smoke = command.get("scripted_smoke", False)
    deployment.p2.old.check_id(run_id)
    if not run_id.startswith(("p4-s1-", "p4-s2-") if smoke else "p4-m1-"):
        raise ValueError("Unapproved subject run identifier")
    folder = HERE / "data" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    clients, retained, futures, stopped = {}, [], [], threading.Event()
    actor_locks = {actor: threading.Lock() for actor in ("alice", "bob")}
    status = {"complete": False, "run_id": run_id, "scripted": smoke,
              "source_freeze_sha256": gate.sha(HERE / "SOURCES-FROZEN.json")}
    with (deployment.P2 / "collection.lock").open("a") as common_lock:
        fcntl.flock(common_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            public = deployment.provision(run_id, configuration, smoke=smoke)
            (folder / "provision.json").write_text(json.dumps(public, indent=2))
            for actor in ("alice", "bob"):
                clients[actor] = deployment.Client(run_id, actor, public)
            emit({"ready": True, "run_id": run_id, "public": public,
                  "routes": {actor: deployment.route(public, actor) for actor in clients}})

            def dispatch(job):
                identifier, actor = job["id"], job["actor"]
                try:
                    if actor not in clients or stopped.is_set():
                        raise RuntimeError("RPC stopped or invalid actor")
                    with actor_locks[actor]:
                        result = clients[actor].batch(job["requests"])
                    record = {"id": identifier, "actor": actor, "request": job,
                              "return_utc_ns": time.time_ns(), **result}
                    append(folder / "actions.jsonl", record)
                    retained.append(record)
                    if not result["complete"]:
                        stopped.set()
                    emit(record)
                except Exception as error:
                    stopped.set()
                    record = {"id": identifier, "actor": actor, "complete": False,
                              "failure_type": type(error).__name__, "request": job}
                    append(folder / "actions.jsonl", record)
                    retained.append(record)
                    emit(record)

            with ThreadPoolExecutor(max_workers=2) as pool:
                seen = set()
                closed = False
                for line in sys.stdin:
                    message = json.loads(line)
                    if message["op"] == "close":
                        closed = True
                        break
                    if message["op"] != "batch" or not isinstance(message.get("id"), int) \
                            or isinstance(message["id"], bool) or message["id"] in seen \
                            or len(seen) >= 2 * 12 or message["actor"] not in clients:
                        raise ValueError("Invalid or repeated RPC job")
                    seen.add(message["id"])
                    futures.append(pool.submit(dispatch, message))
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
            time.sleep(0.10)
            if deployment.p2.old.state_path(run_id).exists():
                try:
                    captured = deployment.snapshot(run_id)
                    (folder / "server.json").write_text(json.dumps(captured["server"]))
                    (folder / "server-log-lines.txt").write_text(
                        "\n".join(sorted(set(captured["server_log_lines"]))) + "\n")
                except Exception as error:
                    status.update(complete=False, evidence_failure_type=type(error).__name__)
            try:
                cleaned = deployment.cleanup(run_id)
                (folder / "cleanup.json").write_text(json.dumps(cleaned, indent=2))
                status["cleanup_absence_proved"] = cleaned["absence_proved"]
                status["complete"] = status["complete"] and cleaned["absence_proved"]
            except Exception as error:
                status.update(complete=False, cleanup_failure_type=type(error).__name__)
            status["batches"] = len(retained)
            status["http_requests"] = sum(len(row.get("records", [])) for row in retained)
            status["file_sha256"] = {file.name: gate.sha(file) for file in folder.iterdir()
                                     if file.is_file()}
            (folder / "sealed.json").write_text(json.dumps(status, indent=2))
            emit({"closed": True, "status": status})


if __name__ == "__main__":
    main()
