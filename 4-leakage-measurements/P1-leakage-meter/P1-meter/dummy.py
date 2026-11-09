#!/usr/bin/env python3
"""Private sham target. It has no network path to Artifactory."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_GET


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
