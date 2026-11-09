#!/usr/bin/env python3
"""Fixed repository policies. No client-controlled policy header or redirects."""
import base64
import http.client
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

CONFIG = json.load(open("/gateway.json"))


def binding_allows(client_ip, auth_header):
    bindings = CONFIG.get("origin_binding")
    if not bindings:
        return True
    expected = bindings.get(client_ip)
    try:
        scheme, encoded = auth_header.split(" ", 1)
        if scheme.lower() != "basic":
            return False
        claimed = base64.b64decode(encoded, validate=True).decode().split(":", 1)[0]
    except (ValueError, UnicodeError):
        return False
    # Artifactory still authenticates the password. The gateway restricts which
    # principal may be presented from each isolated, host-controlled client network.
    return expected is not None and claimed == expected


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def serve(self):
        started = time.time_ns()
        parts = urlsplit(self.path)
        decoded = unquote(parts.path)
        route = decoded.removeprefix("/artifactory/")
        metadata = route.startswith("api/storage/")
        repo = route.split("/")[2] if metadata else route.split("/")[0]
        base_repo = repo.removesuffix("-cache")
        policy = CONFIG["repos"].get(base_repo)
        reason = None
        if not binding_allows(self.client_address[0], self.headers.get("Authorization", "")):
            reason = "origin-identity"
        elif self.command not in ("GET", "HEAD"):
            reason = "method"
        elif not decoded.startswith("/artifactory/") or policy is None:
            reason = "scope"
        elif any(x in decoded for x in ("..", "\\", "\x00")) or parts.netloc:
            reason = "path"
        elif metadata and policy["block_metadata"]:
            reason = "metadata"
        elif route.startswith("api/") and not metadata:
            reason = "api"
        if reason:
            status, payload = 403 if reason != "method" else 405, b"Blocked by fixed lab gateway policy\n"
            response_headers = {"Content-Type": "text/plain"}
        else:
            conn = http.client.HTTPConnection("art-a", 8082, timeout=30)
            try:
                headers = {
                    k: self.headers[k]
                    for k in ("Authorization", "User-Agent", "If-None-Match", "If-Modified-Since")
                    if k in self.headers
                }
                conn.request(self.command, self.path, headers=headers)
                response = conn.getresponse()
                status, payload = response.status, response.read()
                response_headers = {
                    k: v for k, v in response.getheaders()
                    if k.lower() not in ("transfer-encoding", "connection", "content-length")
                }
            except Exception as exc:
                status, payload, response_headers = 502, type(exc).__name__.encode(), {}
                reason = "upstream-error"
            finally:
                conn.close()
        self.send_response(status)
        for key, value in response_headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
        print(json.dumps({
            "start_utc_ns": started, "end_utc_ns": time.time_ns(),
            "method": self.command, "path": self.path, "status": status,
            "policy_reason": reason, "body_bytes": len(payload),
            "client_ip": self.client_address[0],
            "principal": CONFIG.get("origin_binding", {}).get(self.client_address[0])
                         if binding_allows(self.client_address[0], self.headers.get("Authorization", "")) else None,
            "marker": self.headers.get("User-Agent"),
        }), flush=True)

    do_GET = serve
    do_HEAD = serve
    do_PUT = serve
    do_POST = serve
    do_DELETE = serve
    do_MKCOL = serve


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
