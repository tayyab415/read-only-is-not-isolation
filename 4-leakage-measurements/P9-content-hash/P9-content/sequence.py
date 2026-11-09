"""Run one fixed P9 split under the lab2 common lock; fitting remains local."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import time

import analyze
import audit
import p2b
from gate import check_gate

HERE = Path(__file__).resolve().parent
HELD_START = datetime(2026, 11, 9, 7, 45, tzinfo=timezone.utc)
WORK_END = datetime(2026, 11, 9, 9, 30, tzinfo=timezone.utc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("smoke", "calibration", "holdout"))
    split = parser.parse_args().split
    check_gate()
    now = datetime.now(timezone.utc)
    if now >= WORK_END or split == "holdout" and now >= HELD_START:
        raise RuntimeError("Preserve the fixed study window and owned cleanup time")
    status_path = HERE / (split + "-sequence-status.json")
    if status_path.exists():
        raise RuntimeError("Never reuse a retained P9 split")
    if split != "smoke":
        prior = json.loads((HERE / "data/audit-smoke.json").read_text())
        if not prior["passed"] or len(prior["runs"]) != 4 \
                or any(not row["passed"] or row["trials"] != 8 for row in prior["runs"]):
            raise RuntimeError("Every native smoke cell must pass before main collection")
    if split == "holdout":
        analyze.verify_freeze(HERE / "data")
    status = {"split": split, "stage": "collecting", "finished": [],
              "utc_ns": time.time_ns()}
    with (p2b.P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for arm in p2b.ARMS:
                if datetime.now(timezone.utc) >= WORK_END:
                    raise RuntimeError("No new cell after the owned-work cutoff")
                status.update(arm=arm, utc_ns=time.time_ns())
                status_path.write_text(json.dumps(status, indent=2))
                p2b.execute(arm, split)
                status["finished"].append(arm)
            runs = [audit.audit_arm(HERE / "data" / arm, arm, split) for arm in p2b.ARMS]
            result = {
                "split": split, "passed": all(row["passed"] for row in runs), "runs": runs,
                "audit_source_sha256": p2b.sha(HERE / "audit.py"),
                "source_manifest_sha256": p2b.sha(HERE / "SOURCES-SHA256.txt")}
            with (HERE / "data" / ("audit-" + split + ".json")).open("x") as stream:
                json.dump(result, stream, indent=2)
            if not result["passed"]:
                raise RuntimeError("P9 complete native split audit failed")
            status.update(stage="audited", utc_ns=time.time_ns())
        except Exception as error:
            status.update(stage="stopped", failure_type=type(error).__name__,
                          utc_ns=time.time_ns())
            raise
        finally:
            status_path.write_text(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
