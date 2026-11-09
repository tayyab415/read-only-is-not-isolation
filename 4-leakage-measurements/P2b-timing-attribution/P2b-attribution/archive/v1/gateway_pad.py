"""P1's fixed route policy, with independent header and response records."""
import argparse
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlsplit

FORWARD = ("Authorization", "User-Agent", "If-None-Match", "If-Modified-Since", "Range")


def principal(config, address, authorization):
    expected = config["origin_binding"].get(address)
    try:
        scheme, encoded = authorization.split(" ", 1)
        claimed = base64.b64decode(encoded, validate=True).decode().split(":", 1)[0]
    except (ValueError, UnicodeError):
        return None
    return claimed if expected == claimed and scheme.lower() == "basic" else None


def handler(config, events=None):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def serve(self):
            started = time.time_ns()
            parts = urlsplit(self.path)
            decoded = unquote(parts.path)
            route = decoded.removeprefix("/artifactory/")
            storage = route.startswith("api/storage/")
            repo = route.split("/")[2] if storage and len(route.split("/")) > 2 else route.split("/")[0]
            repo = repo.removesuffix("-cache")
            identity = principal(config, self.client_address[0], self.headers.get("Authorization", ""))
            reason = None
            if identity is None:
                reason = "origin-identity"
            elif self.command not in ("GET", "HEAD"):
                reason = "method"
            elif not decoded.startswith("/artifactory/") or repo not in config["repos"]:
                reason = "scope"
            elif ".." in decoded.split("/") or "\\" in decoded:
                reason = "path"
            elif route.startswith("api/") and not storage:
                reason = "api"
            elif storage and config["repos"][repo]["block_storage"]:
                reason = "storage"
            incoming = [(name.lower(), self.headers[name]) for name in
                        ("If-None-Match", "If-Modified-Since", "Range") if name in self.headers]
            if reason:
                status = 405 if reason == "method" else 403
                body, response_headers = b"Blocked by fixed P1 gateway policy\n", [("Content-Type", "text/plain")]
            else:
                connection = http.client.HTTPConnection(config.get("backend_host", "art-a"),
                                                        config.get("backend_port", 8082), timeout=30)
                try:
                    connection.request(self.command, self.path, headers={
                        name: self.headers[name] for name in FORWARD if name in self.headers})
                    response = connection.getresponse()
                    status, body = response.status, response.read()
                    response_headers = [(name, value) for name, value in response.getheaders()
                                        if name.lower() not in ("transfer-encoding", "connection", "content-length")]
                except Exception as exc:
                    status, body, response_headers = 502, type(exc).__name__.encode(), []
                    reason = "upstream-error"
                finally:
                    connection.close()
            floor_ns = int(config.get("min_response_ms", 0)) * 1_000_000
            if floor_ns:
                wait_ns = started + floor_ns - time.time_ns()
                if wait_ns > 0:
                    time.sleep(wait_ns / 1e9)
            try:
                self.send_response(status)
                for name, value in response_headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            finally:
                delivered = body if self.command != "HEAD" else b""
                record = {"start_utc_ns": started, "end_utc_ns": time.time_ns(),
                          "method": self.command, "path": self.path, "status": status,
                          "policy_reason": reason, "principal": identity,
                          "client_ip": self.client_address[0], "marker": self.headers.get("User-Agent"),
                          "request_headers": incoming, "body_bytes": len(delivered),
                          "body_sha256": hashlib.sha256(delivered).hexdigest(),
                          "gateway_edition": 2, "min_response_ms": config.get("min_response_ms", 0)}
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
    parser.add_argument("--config", type=Path, default=Path("/gateway.json"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    queue_size = config.get("queue_size", 5)
    if queue_size not in (5, 128):
        raise ValueError("Unapproved gateway queue size")

    class Server(ThreadingHTTPServer):
        request_queue_size = queue_size

    server = Server(("0.0.0.0", args.port), handler(config))
    print(json.dumps({"ready": True, "edition": 2, "queue_size": queue_size}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
