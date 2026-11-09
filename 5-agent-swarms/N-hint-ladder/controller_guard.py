"""One local controller at a time, and a persistent stop after unsafe cleanup."""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
STOP_PATH = ROOT / "cleanup-stop.json"


@contextmanager
def controller_lock():
    with (ROOT / "controller.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another N controller holds controller.lock")
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def stop_launches(cohort_id, reason):
    STOP_PATH.write_text(json.dumps({"cohort_id": cohort_id, "reason": reason,
                                    "utc_ns": time.time_ns()}, indent=2))
