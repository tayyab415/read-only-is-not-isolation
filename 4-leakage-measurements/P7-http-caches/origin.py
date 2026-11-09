"""Owned stateless fixture, matching J's bytes and miss delays."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import time

PAYLOAD = b"J controls harmless artifact fixture v1\n"
MISSING = b"Missing harmless fixture\n"
PATH = re.compile(r"/(?:missing/(20|150)|present)/[a-zA-Z0-9_.-]+")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def serve(self):
        started = time.time_ns()
        matched = PATH.fullmatch(self.path)
        if not matched or self.command not in ("GET", "HEAD"):
            status, body, delay_ms = 400, b"Outside the owned fixture\n", 0
        elif matched[1]:
            delay_ms = int(matched[1])
            time.sleep(delay_ms / 1000)
            status, body = 404, MISSING
        else:
            delay_ms, status, body = 0, 200, PAYLOAD
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if status == 200:
            self.send_header("Last-Modified", "Sat, 07 Nov 2026 00:00:00 GMT")
            self.send_header("ETag", '"' + hashlib.sha256(body).hexdigest() + '"')
        self.end_headers()
        delivered = body if self.command != "HEAD" else b""
        if delivered:
            self.wfile.write(delivered)
        print(json.dumps({"start_utc_ns": started, "end_utc_ns": time.time_ns(),
            "client_ip": self.client_address[0], "method": self.command, "path": self.path,
            "marker": self.headers.get("User-Agent"), "status": status, "delay_ms": delay_ms,
            "body_bytes": len(delivered), "body_sha256": hashlib.sha256(delivered).hexdigest(),
            "request_headers": {name: self.headers.get(name) for name in ("Range", "If-None-Match")}}), flush=True)

    do_GET = serve
    do_HEAD = serve


if __name__ == "__main__":
    print(json.dumps({"ready": True, "stateless": True, "outbound_requests": 0}), flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
