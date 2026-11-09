"""Owned application-isolation comparator with frozen response profiles.

It approximates wire bytes and cold/warm delays. It does not reproduce JVM,
database, gateway, or host scheduling costs. No upstream connection exists.
"""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit


def handler(profile, events=None):
    required = {"get", "head", "cond_get", "range_get", "fresh_get",
                "repeated_cold", "repeated_warm", "folder_html", "stats"}
    if set(profile["responses"]) != required:
        raise ValueError("The complete frozen response profile is required")
    for row in profile["responses"].values():
        if not 0 <= row["delay_s"] <= 1 or not 100 <= row["status"] <= 599:
            raise ValueError("Invalid response profile")
        base64.b64decode(row["body_b64"], validate=True)
        if any(name.lower() in {"authorization", "set-cookie", "proxy-authorization"}
               for name, _ in row["headers"]):
            raise ValueError("A response profile contains a private header")
    lock, seen = threading.Lock(), set()
    repo = profile["repository"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def serve(self):
            started = time.time_ns()
            path = urlsplit(self.path).path
            if self.command not in ("GET", "HEAD"):
                self.send_error(405)
                return
            if path == f"/artifactory/api/storage/{repo}-cache/pkg-15.bin":
                kind = "stats"
            elif path == f"/artifactory/{repo}-cache/":
                kind = "folder_html"
            elif path == f"/artifactory/{repo}/pkg-00.bin":
                if self.headers.get("If-None-Match"):
                    kind = "cond_get"
                elif self.headers.get("Range"):
                    kind = "range_get"
                else:
                    kind = "head" if self.command == "HEAD" else "get"
            elif path.startswith(f"/artifactory/{repo}/fresh/"):
                if path.endswith("-repeat.bin"):
                    with lock:
                        kind = "repeated_warm" if path in seen else "repeated_cold"
                        seen.add(path)
                else:
                    kind = "fresh_get"
            else:
                self.send_error(403)
                return
            response = profile["responses"][kind]
            time.sleep(response["delay_s"])
            body = base64.b64decode(response["body_b64"])
            self.send_response_only(response["status"])
            for name, value in response["headers"]:
                if name.lower() not in ("content-length", "connection", "transfer-encoding"):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD" and response["status"] != 304:
                self.wfile.write(body)
            record = {"start_utc_ns": started, "end_utc_ns": time.time_ns(),
                      "method": self.command, "path": self.path, "kind": kind,
                      "status": response["status"], "profile_delay_s": response["delay_s"],
                      "client_ip": self.client_address[0],
                      "marker": self.headers.get("User-Agent"),
                      "request_headers": {name.lower(): self.headers[name]
                                          for name in ("Range", "If-None-Match", "If-Modified-Since")
                                          if name in self.headers}}
            if events is None:
                print(json.dumps(record), flush=True)
            else:
                with lock:
                    events.append(record)

        do_GET = serve
        do_HEAD = serve
        do_POST = serve
        do_PUT = serve
        do_DELETE = serve

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    # The comparator has a private accept queue, as it has a private process.
    # Its queue size is recorded, not asserted equivalent to Artifactory.
    class Server(ThreadingHTTPServer):
        request_queue_size = 128

    server = Server(("0.0.0.0", args.port), handler(profile))
    print(json.dumps({"ready": True, "queue_size": server.request_queue_size}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
