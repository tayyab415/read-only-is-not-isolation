"""Collect one owned P2 split/configuration. No analysis or paid calls."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

import harness
import plan
import service

HERE = Path(__file__).resolve().parent


def verify_model_freeze(data):
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    if not frozen["holdout_collection_allowed"] or data.resolve() != (HERE / frozen["data"]).resolve():
        raise RuntimeError("Holdout collection has not been allowed")
    for name, expected in frozen["sha256"].items():
        if service.sha(HERE / name) != expected:
            raise RuntimeError("A frozen calibration source changed")


def execute(data, configuration, split, smoke=False):
    service.check_gate()
    if split == "holdout":
        if smoke:
            raise ValueError("Smoke is an implementation calibration run only")
        verify_model_freeze(data)
    if data.name != ("smoke-p2-s1" if smoke else "p2-m1"):
        raise ValueError("Unapproved dataset identifier")
    count = 8 if smoke else (300 if split == "calibration" else 1000)
    run_id = f"p2-{'s1' if smoke else 'm1'}-{split[0]}-{configuration}"
    service.old.check_id(run_id)
    folder = data / configuration
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{split}.jsonl"
    if output.exists():
        raise RuntimeError("This split already has retained records")
    schedule = plan.schedule(configuration, split, count)
    if len(schedule) != count:
        raise RuntimeError("The schedule does not meet its declared denominator")
    (folder / f"{split}-schedule.json").write_text(json.dumps(schedule))
    clients, dummy_clients, records, lines = {}, {}, 0, set()
    status = {"complete": False, "run_id": run_id, "split": split}
    try:
        public = service.provision(run_id, configuration)
        (folder / f"{split}-provision.json").write_text(json.dumps(public, indent=2))
        state = service.old.load_state(run_id)
        for actor in ("alice", "bob"):
            clients[actor] = harness.Client.sandbox(service.old, run_id, actor)
        if configuration == "sham":
            for actor in ("alice", "bob"):
                dummy_clients[actor] = harness.Client.sandbox(service.old, run_id, actor, "dummy")
        with output.open("x") as stream:
            for trial in schedule:
                record = harness.run_trial(state, run_id, configuration, trial, clients, dummy_clients)
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                records += 1
                print(json.dumps({"run_id": run_id, "trial": trial["trial_id"],
                                  "split": split, "retained_trials": records}), flush=True)
                if record["batch_must_stop"]:
                    raise RuntimeError("A request failed; stop the batch")
                if records % 100 == 0 and configuration != "immutable":
                    lines.update(service.server_log_lines(run_id))
                time.sleep(0.5)
        status["complete"] = records == count
    except Exception as error:
        # Keep exception type, rather than risk exporting private configuration.
        status["failure_type"] = type(error).__name__
        raise
    finally:
        for client in list(clients.values()) + list(dummy_clients.values()):
            try:
                client.close()
            except Exception as error:
                status.update(complete=False, worker_stop_failure_type=type(error).__name__)
        time.sleep(0.10)  # Allow trusted completion records to flush before the snapshot.
        if service.old.state_path(run_id).exists():
            try:
                server, recent = service.collect(run_id)
                lines.update(recent)
                (folder / f"{split}-server.json").write_text(json.dumps(server))
                (folder / f"{split}-server-log-lines.txt").write_text("\n".join(sorted(lines)) + "\n")
            except Exception as error:
                status.update(complete=False, evidence_failure_type=type(error).__name__)
        cleanup = service.cleanup(run_id)
        (folder / f"{split}-cleanup.json").write_text(json.dumps(cleanup, indent=2))
        status.update(retained_trials=records, cleanup_absence_proved=cleanup["absence_proved"])
        if not cleanup["absence_proved"]:
            status["complete"] = False
        if status["complete"] and cleanup["absence_proved"]:
            status.update(sha256=service.sha(output), trials=records,
                          provision_sha256=service.sha(folder / f"{split}-provision.json"),
                          sealed_utc_ns=time.time_ns(),
                          evidence_sha256={name: service.sha(folder / name) for name in [
                              f"{split}-schedule.json", f"{split}-server.json",
                              f"{split}-server-log-lines.txt", f"{split}-cleanup.json"]})
        (folder / f"{split}-sealed.json").write_text(json.dumps(status, indent=2))
        if not cleanup["absence_proved"]:
            raise RuntimeError("Owned runtime cleanup is incomplete")
    if not status["complete"]:
        raise RuntimeError("Collection evidence is incomplete")
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("calibration", "holdout"))
    parser.add_argument("configuration", choices=plan.CONFIGURATIONS)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    data = HERE / "data" / ("smoke-p2-s1" if args.smoke else "p2-m1")
    with (HERE / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(execute(data, args.configuration, args.split, args.smoke)))


if __name__ == "__main__":
    main()
