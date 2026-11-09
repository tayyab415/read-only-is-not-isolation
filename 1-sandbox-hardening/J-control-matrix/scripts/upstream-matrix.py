#!/usr/bin/env python3
"""A stateless local fixture. It never follows or issues outbound requests."""
import hashlib
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAYLOAD = b"J controls harmless artifact fixture v1\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def serve(self):
        started = time.time_ns()
        path = self.path.split("?", 1)[0]
        if path.startswith("/missing/"):
            delay_ms = int(path.split("/")[2])
            if delay_ms not in (20, 150):
                self.send_error(400)
                return
            time.sleep(delay_ms / 1000)
            status, body = 404, b"Missing harmless fixture\n"
        else:
            delay_ms, status, body = 0, 200, PAYLOAD
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if status == 200:
            self.send_header("Last-Modified", "Sat, 07 Nov 2026 00:00:00 GMT")
            self.send_header("ETag", '"' + hashlib.sha256(body).hexdigest() + '"')
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


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
