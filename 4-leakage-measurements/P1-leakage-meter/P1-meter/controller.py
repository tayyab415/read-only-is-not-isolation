#!/usr/bin/env python3
"""Collect P1 calibration first, freeze, then retrieve sealed holdout.

This controller does not alter the remote experiment or make model calls.
Run with --once for a progress read only, or --run to advance the analysis
when its gates are satisfied. Source and fitted-model hashes remain mandatory.
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = ["open", "combined", "separate", "aa", "sham"]
CAL = HERE / "data/main-m1-calibration"
FULL = HERE / "data/main-m1"
OUT = HERE / "results/analysis-v2/main-m1"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
       "-o", "ControlMaster=auto", "-o", "ControlPersist=10m",
       "-o", "ControlPath=/tmp/p1-research-cm-%r@%h:%p", "labhost"]
REMOTE = "workspaces/artifactory-lab/p1/data/m1"
PUBLIC_FILES = {"calibration.jsonl", "provision.json", "holdout.jsonl",
                "sealed.json", "cleanup.json", "server.json", "server-log-lines.txt"}
REMOTE_STATUS = r'''
from pathlib import Path
import hashlib, json, subprocess
root = Path.home() / "workspaces/artifactory-lab/p1/data/m1"
result = {}
for run in ["open", "combined", "separate", "aa", "sham"]:
    d = root / run
    cal = d / "calibration.jsonl"
    n = sum(1 for _ in cal.open()) if cal.exists() else 0
    row = {"calibration_trials": n}
    if n == 300:
        row["calibration_sha256"] = hashlib.sha256(cal.read_bytes()).hexdigest()
        row["provision_sha256"] = hashlib.sha256((d / "provision.json").read_bytes()).hexdigest()
    seal = d / "sealed.json"
    if seal.exists():
        row["seal"] = json.loads(seal.read_text())
    log = Path.home() / f"logs/p1-m1-{run}.log"
    if log.exists():
        for line in reversed(log.read_text().splitlines()[-10:]):
            try:
                v = json.loads(line)
                row["progress"] = {k: v[k] for k in ["run", "trial", "split", "complete"] if k in v}
                break
            except ValueError:
                pass
    result[run] = row
result["collection_running"] = subprocess.run(
    ["tmux", "has-session", "-t", "p1-main"], capture_output=True).returncode == 0
status = Path.home() / "logs/p1-m1.status"
result["collection_status"] = status.read_text() if status.exists() else ""
print(json.dumps(result))
'''


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=kwargs.pop("timeout", 120), **kwargs)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed with status {result.returncode}: {result.stderr[-500:]}")
    return result.stdout


def status():
    return json.loads(command(SSH + ["python3 -"], input=REMOTE_STATUS))


def policy_check():
    frozen = json.loads((HERE / "ANALYSIS-CODE-FROZEN.json").read_text())
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Rule source changed: {name}")
    regression = json.loads((HERE / "analysis-regression-v2.json").read_text())
    if not regression["passed"] or regression["analysis_sha256"] != frozen["sha256"]["analysis.py"] \
            or regression["test_source_sha256"] != frozen["sha256"]["test_analysis.py"]:
        raise RuntimeError("Analysis regression gate has not passed")


def fetch(run, name, destination, expected=None, frozen=False):
    if run not in RUNS or name not in PUBLIC_FILES:
        raise ValueError("File outside the public evidence allowlist")
    if name == "holdout.jsonl" and not frozen:
        raise RuntimeError("Holdout retrieval requires the final freeze")
    target = destination / run / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and expected is not None and sha(target) == expected:
        return
    temporary = target.with_suffix(target.suffix + ".retrieving")
    command(["rsync", "-a", f"labhost:{REMOTE}/{run}/{name}", str(temporary)], timeout=300)
    if expected is not None and sha(temporary) != expected:
        raise RuntimeError(f"Remote/local hash mismatch: {run}/{name}")
    temporary.replace(target)


def analysis(mode, data):
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    text = command([sys.executable, str(HERE / "analysis.py"), mode, str(data), str(OUT)],
                   env=env, timeout=1800)
    with (HERE / "controller.log").open("a") as stream:
        stream.write(f"{utc()} {mode}\n{text}\n")


def advance(snapshot):
    policy_check()
    if not all(snapshot[r]["calibration_trials"] == 300 for r in RUNS):
        return {"stage": "collecting_calibration", "holdout_retrieved": False}
    for run in RUNS:
        fetch(run, "calibration.jsonl", CAL, snapshot[run]["calibration_sha256"])
        fetch(run, "provision.json", CAL, snapshot[run]["provision_sha256"])
    final = HERE / "FROZEN.json"
    if not final.exists():
        analysis("calibrate", CAL)
        analysis("freeze", CAL)
    freeze = json.loads(final.read_text())
    if freeze["output_dir"] != str(OUT.relative_to(HERE)):
        raise RuntimeError("Final freeze points at another analysis directory")
    for name, expected in freeze["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Post-freeze change: {name}")
    if not all(snapshot[r].get("seal", {}).get("files", {}).get("holdout", {}).get("trials") == 1000
               for r in RUNS):
        return {"stage": "models_frozen_waiting_for_seals", "holdout_retrieved": False,
                "freeze_utc": freeze["utc"]}
    for run in RUNS:
        seal = snapshot[run]["seal"]["files"]
        if seal["calibration"]["sha256"] != snapshot[run]["calibration_sha256"]:
            raise RuntimeError(f"Calibration/seal disagreement: {run}")
        for name, expected in (
            ("calibration.jsonl", seal["calibration"]["sha256"]),
            ("provision.json", snapshot[run]["provision_sha256"]),
            ("holdout.jsonl", seal["holdout"]["sha256"]),
            ("sealed.json", None), ("cleanup.json", None),
            ("server.json", None), ("server-log-lines.txt", None),
        ):
            fetch(run, name, FULL, expected, frozen=True)
        cleanup = json.loads((FULL / run / "cleanup.json").read_text())
        if not cleanup["absence_proved"]:
            raise RuntimeError(f"Remote cleanup is unproved: {run}")
    if not (OUT / "holdout-results.json").exists():
        analysis("holdout", FULL)
    return {"stage": "analysis_ready_for_audit", "holdout_retrieved": True,
            "freeze_utc": freeze["utc"], "results_sha256": sha(OUT / "holdout-results.json"),
            "interpretation": "Lead must verify joins, nulls, positive controls and multiplicity before conclusions."}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true")
    group.add_argument("--run", action="store_true")
    parser.add_argument("--interval", type=int, default=120)
    args = parser.parse_args()
    with (HERE / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        failures = 0
        while True:
            try:
                snapshot = status()
                stage = advance(snapshot) if args.run else {"stage": "status_only"}
                record = {"utc": utc(), "pid": os.getpid(), **stage, "collection": snapshot}
                save(HERE / "controller-status.json", record)
                print(json.dumps({k: record[k] for k in ["utc", "stage"]}), flush=True)
                failures = 0
                if args.once or stage["stage"] == "analysis_ready_for_audit":
                    return
                if "FAIL" in snapshot["collection_status"]:
                    raise RuntimeError("Remote collection failed; inspect it before resuming")
                if not snapshot["collection_running"]:
                    raise RuntimeError("Collection stopped before all gates were satisfied")
            except Exception as error:
                failures += 1
                save(HERE / "controller-error.json",
                     {"utc": utc(), "type": type(error).__name__, "message": str(error),
                      "consecutive_failures": failures, "remote_collection_altered": False})
                if failures >= 3 or args.once:
                    raise
            time.sleep(max(15, args.interval))


if __name__ == "__main__":
    main()
