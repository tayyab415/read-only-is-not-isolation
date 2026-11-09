"""One prospective P8 cell, under the shared experiment lock."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import time

from experiment import CONFIGURATIONS, PAYLOAD, digest, executing, schedule
import runtime
from telemetry import inventory, scheduled_samples

HERE = Path(__file__).resolve().parent


def verify_freeze(data):
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    if frozen.get("holdout_collection_allowed") is not True \
            or (HERE / frozen["data"]).resolve() != data.resolve():
        raise RuntimeError("P8 holdout has not been authorized")
    for name, expected in frozen["sha256"].items():
        if runtime.sha(HERE / name) != expected:
            raise RuntimeError("A frozen P8 calibration input changed")
    if any((data / arm / "holdout.jsonl").exists() for arm in CONFIGURATIONS) \
            and frozen.get("all_models_frozen_before_holdout") is not True:
        raise RuntimeError("P8 holdout lacks the common freeze")
    return frozen


def run_trial(trial, configuration, actors, executor):
    epoch = time.time_ns() + 700_000_000
    record = {**trial, "configuration": configuration, "epoch_utc_ns": epoch,
              "controller_start_utc_ns": time.time_ns()}
    base = {"op": "trial", "epoch_utc_ns": epoch, "token": trial["token"]}
    actors["alice"].send({**base, "execute": executing(configuration, trial["label"])})
    actors["bob"].send(base)
    host = executor.submit(scheduled_samples, epoch, False)
    failures = []
    for actor in ("alice", "bob"):
        try:
            result = actors[actor].read()
            record["sender" if actor == "alice" else "receiver"] = result
            if result.get("rpc") != ({**base, "execute": executing(configuration, trial["label"])}
                                     if actor == "alice" else base):
                failures.append(actor + "_rpc_mismatch")
            if result.get("file", {}).get("successful") is not True:
                failures.append(actor + "_file_failure")
            if any(row.get("status") != 200 or row.get("body_sha256") != digest(PAYLOAD)
                   or row.get("body_length") != len(PAYLOAD) for row in result.get("http", [])) \
                    or len(result.get("http", [])) != 2:
                failures.append(actor + "_http_failure")
        except Exception as error:
            failures.append(actor + "_" + type(error).__name__)
    try:
        record["host_samples"] = host.result(timeout=5)
    except Exception as error:
        failures.append("host_" + type(error).__name__)
    record.update(controller_end_utc_ns=time.time_ns(), failures=failures, batch_must_stop=bool(failures))
    return record


def execute(configuration, split):
    runtime.check_gate()
    data = HERE / "data" / ("smoke-p8-s1" if split == "smoke" else "p8-m1")
    if split != "smoke":
        runtime.check_smoke()
    if split == "holdout":
        verify_freeze(data)
    run_id = f"p8-{'s1' if split == 'smoke' else 'm1'}-{split[0]}-{configuration}"
    runtime.old.check_id(run_id)
    folder = data / configuration
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{split}.jsonl"
    if output.exists():
        raise RuntimeError("Never reuse a retained P8 cell")
    jobs = schedule(configuration, split)
    (folder / f"{split}-schedule.json").write_text(json.dumps(jobs))
    status = {"complete": False, "configuration": configuration, "split": split, "run_id": run_id}
    actors, retained = {}, 0
    try:
        public = runtime.provision(run_id, configuration)
        public["host_inventory"] = inventory()
        (folder / f"{split}-provision.json").write_text(json.dumps(public, indent=2))
        state = runtime.old.load_state(run_id)
        for actor in ("alice", "bob"):
            actors[actor] = runtime.Participant(state, actor)
        (folder / f"{split}-readiness.json").write_text(json.dumps({
            actor: participant.readiness for actor, participant in actors.items()}, indent=2))
        with output.open("x") as stream, ThreadPoolExecutor(max_workers=1) as executor:
            for trial in jobs:
                record = run_trial(trial, configuration, actors, executor)
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                retained += 1
                print(json.dumps({"run_id": run_id, "split": split, "retained": retained}), flush=True)
                if record["batch_must_stop"]:
                    raise RuntimeError("A fixed P8 task failed")
        status["complete"] = retained == len(jobs)
    except Exception as error:
        status["failure_type"] = type(error).__name__
        raise
    finally:
        for actor, participant in actors.items():
            try:
                participant.close()
            except Exception as error:
                status.update(complete=False, worker_stop_failure_type=type(error).__name__)
        time.sleep(.1)
        if runtime.old.state_path(run_id).exists():
            try:
                server, _ = runtime.p2.collect(run_id)
                (folder / f"{split}-server.json").write_text(json.dumps(server))
            except Exception as error:
                status.update(complete=False, evidence_failure_type=type(error).__name__)
        cleanup = runtime.p2.cleanup(run_id)
        (folder / f"{split}-cleanup.json").write_text(json.dumps(cleanup, indent=2))
        status.update(retained_trials=retained, cleanup_absence_proved=cleanup["absence_proved"])
        if not cleanup["absence_proved"]:
            status["complete"] = False
        if status["complete"]:
            evidence = [f"{split}-{name}.json" for name in ("schedule", "provision", "readiness",
                                                           "server", "cleanup")]
            status.update(sha256=runtime.sha(output), trials=retained, sealed_utc_ns=time.time_ns(),
                          evidence_sha256={name: runtime.sha(folder / name) for name in evidence})
        (folder / f"{split}-sealed.json").write_text(json.dumps(status, indent=2))
        if not status["complete"]:
            raise RuntimeError("P8 collection or cleanup is incomplete")
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("smoke", "calibration", "holdout"))
    parser.add_argument("configuration", choices=CONFIGURATIONS)
    args = parser.parse_args()
    with (runtime.P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(execute(args.configuration, args.split)))


if __name__ == "__main__":
    main()
