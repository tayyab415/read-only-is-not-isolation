"""Append-only request reservations, including possible cache-origin work."""
import fcntl
import json
import os
from pathlib import Path
import re
import time

MAX_CONTROLLER_REQUESTS = 1200
MAX_TOTAL_HTTP_REQUESTS = 2400
MARKER = re.compile(r"p7/[a-zA-Z0-9_./-]{1,160}")


class RequestGuard:
    def __init__(self, path):
        self.path = Path(path)

    def reserve(self, marker, direct_origin=False):
        if not isinstance(marker, str) or not MARKER.fullmatch(marker) or type(direct_origin) is not bool:
            raise ValueError("Invalid owned request reservation")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            rows = [json.loads(line) for line in stream if line.strip()]
            markers = set()
            for row in rows:
                if set(row) != {"marker", "direct_origin", "reserved_http_requests", "utc_ns"} \
                        or not MARKER.fullmatch(row["marker"]) or row["marker"] in markers \
                        or type(row["direct_origin"]) is not bool \
                        or type(row["reserved_http_requests"]) is not int \
                        or row["reserved_http_requests"] != (1 if row["direct_origin"] else 2) \
                        or type(row["utc_ns"]) is not int or row["utc_ns"] <= 0:
                    raise RuntimeError("Request ledger is malformed")
                markers.add(row["marker"])
            slots = 1 if direct_origin else 2
            if marker in markers:
                raise RuntimeError("Duplicate request marker; a retry needs a new reservation")
            if len(rows) + 1 > MAX_CONTROLLER_REQUESTS \
                    or sum(row["reserved_http_requests"] for row in rows) + slots > MAX_TOTAL_HTTP_REQUESTS:
                raise RuntimeError("Request limit reached")
            record = {"marker": marker, "direct_origin": direct_origin,
                      "reserved_http_requests": slots, "utc_ns": time.time_ns()}
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            return record
