"""Run one P8 split sequentially, holding the common lock across all arms."""
import argparse
import fcntl
import json
from pathlib import Path
import time

import audit
from collector import execute
from experiment import CONFIGURATIONS
import runtime

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("smoke", "calibration", "holdout"))
    args = parser.parse_args()
    status = {"stage": "collecting", "split": args.split, "finished": [], "utc_ns": time.time_ns()}
    path = HERE / f"{args.split}-sequence-status.json"
    data = HERE / "data" / ("smoke-p8-s1" if args.split == "smoke" else "p8-m1")
    with (runtime.P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for configuration in CONFIGURATIONS:
                status.update(configuration=configuration, utc_ns=time.time_ns())
                path.write_text(json.dumps(status, indent=2))
                execute(configuration, args.split)
                status["finished"].append(configuration)
            result = audit.audit_all(data, args.split)
            (data / f"{args.split}-audit-results.json").write_text(json.dumps(result, indent=2))
            if not result["passed"]:
                raise RuntimeError("P8 native split audit failed")
            status.update(stage="audited", utc_ns=time.time_ns())
        except Exception as error:
            status.update(stage="stopped", failure_type=type(error).__name__, utc_ns=time.time_ns())
            raise
        finally:
            path.write_text(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
