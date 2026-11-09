#!/usr/bin/env python3
"""Hub-side P2b sequence: wait for P2, smoke + checks, calibration, decoder freeze, holdout.

Stops at the first failed check. Writes sequence-status.json after every step.
"""
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402
import p2b  # noqa: E402

DEADLINE = datetime(2026, 11, 9, 9, 30, tzinfo=timezone.utc)


def status(stage, **extra):
    (HERE / "sequence-status.json").write_text(json.dumps(
        {"utc": datetime.now(timezone.utc).isoformat(), "stage": stage, **extra}, indent=2))
    print(json.dumps({"stage": stage, **extra}), flush=True)


def run(split):
    subprocess.run([sys.executable, str(HERE / "p2b.py"), split], check=True)
    subprocess.run([sys.executable, str(HERE / "audit.py"), str(HERE / "data"), split], check=True)


def smoke_checks():
    out = {}
    for arm in p2b.ARMS:
        trials = analyze.load(HERE / "data" / arm / "smoke.jsonl")
        s = [analyze.statistic(t) for t in trials]
        out[arm] = {"trials": len(trials), "median_S_ms": statistics.median(s),
                    "failed": sum(1 for t in trials if t["failed_request_markers"])}
        if len(trials) != 8 or out[arm]["failed"]:
            raise RuntimeError(f"smoke failed for {arm}")
    prov = json.loads((HERE / "data/pergw/smoke-provision.json").read_text())
    if not prov["negative_route_checks"] or not all(prov["negative_route_checks"].values()):
        raise RuntimeError("pergw negative route checks failed")
    if out["pad"]["median_S_ms"] < p2b.PAD_MS:
        raise RuntimeError("pad arm responses arrived before the floor")
    return out


def main():
    while True:
        if datetime.now(timezone.utc) >= DEADLINE:
            status("deadline_before_start")
            return
        try:
            p2b.p2_finished()
            p2b.p2_gate()
            break
        except SystemExit as reason:
            status("waiting_for_p2", reason=str(reason))
            time.sleep(120)
    status("smoke")
    run("smoke")
    status("smoke_checks", checks=smoke_checks())
    run("calibration")
    frozen = analyze.freeze(HERE / "data")
    status("decoders_frozen", decoders={a: {k: v for k, v in d.items() if k != "calibration_sha256"}
                                        for a, d in frozen["decoders"].items()})
    if datetime.now(timezone.utc) >= DEADLINE:
        status("deadline_before_holdout")
        return
    run("holdout")
    status("holdout_complete")


if __name__ == "__main__":
    main()
