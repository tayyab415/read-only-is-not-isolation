#!/usr/bin/env python3
"""A stateless local fixture for N, adapted from J's upstream.py.

Serves 16 identical present artifacts (pkg-00.bin .. pkg-15.bin); any other
path is a declared-missing path with a 150 ms delay. Stateless: never
follows or issues outbound requests, never writes to disk.
"""
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAYLOAD = b"n-ladder fixture package bytes\n"
PRESENT = re.compile(r"^/pkg-(\d\d)\.bin$")
MISSING_DELAY_MS = 150


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def serve(self):
        started = time.time_ns()
        path = self.path.split("?", 1)[0]
        match = PRESENT.match(path)
        if match and 0 <= int(match.group(1)) <= 15:
            delay_ms, status, body = 0, 200, PAYLOAD
        else:
            delay_ms, status, body = MISSING_DELAY_MS, 404, b"Missing n-ladder fixture\n"
            time.sleep(delay_ms / 1000)
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if status == 200:
            self.send_header("Last-Modified", "Sat, 07 Nov 2026 00:00:00 GMT")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        print(json.dumps({
            "start_utc_ns": started, "end_utc_ns": time.time_ns(),
            "method": self.command, "path": self.path, "status": status,
            "delay_ms": delay_ms, "body_bytes": len(body),
            "marker": self.headers.get("User-Agent"),
        }), flush=True)

    do_GET = serve
    do_HEAD = serve


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
