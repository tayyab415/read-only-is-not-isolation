#!/usr/bin/env python3
"""Fixed policy gateway for P1 fixture repositories."""
import base64
import http.client
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

CONFIG = json.load(open("/gateway.json"))


def bound_principal(client_ip, auth_header):
    expected = CONFIG["origin_binding"].get(client_ip)
    try:
        scheme, encoded = auth_header.split(" ", 1)
        claimed = base64.b64decode(encoded, validate=True).decode().split(":", 1)[0]
    except (ValueError, UnicodeError):
        return None
    return claimed if expected == claimed and scheme.lower() == "basic" else None


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
        principal = bound_principal(self.client_address[0], self.headers.get("Authorization", ""))
        reason = None
        if principal is None:
            reason = "origin-identity"
        elif self.command not in ("GET", "HEAD"):
            reason = "method"
        elif not decoded.startswith("/artifactory/") or repo not in CONFIG["repos"]:
            reason = "scope"
        elif any(part in ("..", "") for part in decoded.split("/") if part == "..") or "\\" in decoded:
            reason = "path"
        elif route.startswith("api/") and not storage:
            reason = "api"
        elif storage and CONFIG["repos"][repo]["block_storage"]:
            reason = "storage"

        if reason:
            status = 405 if reason == "method" else 403
            body = b"Blocked by fixed P1 gateway policy\n"
            response_headers = {"Content-Type": "text/plain"}
        else:
            connection = http.client.HTTPConnection("art-a", 8082, timeout=30)
            try:
                headers = {
                    key: self.headers[key]
                    for key in ("Authorization", "User-Agent", "If-None-Match", "If-Modified-Since", "Range")
                    if key in self.headers
                }
                connection.request(self.command, self.path, headers=headers)
                response = connection.getresponse()
                status, body = response.status, response.read()
                response_headers = {
                    key: value for key, value in response.getheaders()
                    if key.lower() not in ("transfer-encoding", "connection", "content-length")
                }
            except Exception as error:
                status, body, response_headers = 502, type(error).__name__.encode(), {}
                reason = "upstream-error"
            finally:
                connection.close()

        self.send_response(status)
        for key, value in response_headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        print(json.dumps({
            "start_utc_ns": started,
            "end_utc_ns": time.time_ns(),
            "method": self.command,
            "path": self.path,
            "status": status,
            "policy_reason": reason,
            "body_bytes": len(body),
            "client_ip": self.client_address[0],
            "principal": principal,
            "marker": self.headers.get("User-Agent"),
        }), flush=True)

    do_GET = serve
    do_HEAD = serve
    do_POST = serve
    do_PUT = serve
    do_DELETE = serve


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
