#!/usr/bin/env python3
"""Local fixtures only. Logs every HEAD and GET; never fetches any other URL."""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE = os.environ.get("FIXTURE_ROLE", "upstream")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def handle_request(self, head):
        path = self.path.partition("?")[0]
        if ROLE == "upstream" and path.startswith("/redirect/"):
            status, body = 302, b""
            location = "http://h-target:8000/final/fixture.bin"
        elif path.startswith("/missing/"):
            time.sleep(0.15)
            status, body, location = 404, b"missing fixture\n", None
        else:
            status, body, location = 200, (ROLE + ": harmless fixture\n").encode(), None
        print(json.dumps({
            "role": ROLE, "method": self.command, "path": self.path,
            "status": status, "location": location, "utc_ns": time.time_ns(),
        }), flush=True)
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_HEAD(self):
        self.handle_request(True)

    def do_GET(self):
        self.handle_request(False)


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
