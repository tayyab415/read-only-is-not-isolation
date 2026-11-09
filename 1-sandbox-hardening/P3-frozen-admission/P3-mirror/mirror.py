#!/usr/bin/env python3
"""Static Python package index with no request-triggered upstream fetches.

Wheel bytes are verified and loaded before listening. Each serving process
gets its own request threads and log. Application responses depend only on the
frozen catalog and the request. Shared-host timing is outside that property.
"""
import argparse
import hashlib
import html
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def catalog(manifest_path):
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    routes, projects = {}, {}
    for entry in manifest["entries"]:
        path = (root / entry["path"]).resolve()
        if root.resolve() not in path.parents:
            raise ValueError("Manifest path escapes its content directory")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != entry["sha256"] or len(body) != entry["bytes"]:
            raise ValueError(f"Manifest checksum mismatch: {entry['filename']}")
        route = f"/files/{entry['sha256']}/{quote(entry['filename'])}"
        routes[route] = (body, "application/octet-stream")
        projects.setdefault(entry["canonical"], []).append((route, entry))
    for project, entries in projects.items():
        links = []
        for route, entry in entries:
            requires = entry.get("requires_python") or ""
            links.append(f'<a href="{route}#sha256={entry["sha256"]}" '
                         f'data-requires-python="{html.escape(requires, quote=True)}">'
                         f'{html.escape(entry["filename"])}</a><br>')
        body = ("<!doctype html><html><body>" + "\n".join(links) + "</body></html>").encode()
        routes[f"/simple/{project}/"] = (body, "text/html; charset=utf-8")
    listing = "\n".join(f'<a href="/simple/{name}/">{name}</a><br>' for name in sorted(projects))
    routes["/simple/"] = (f"<!doctype html><html><body>{listing}</body></html>".encode(),
                          "text/html; charset=utf-8")
    return routes, manifest


def handler(routes, log_path, tenant):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def serve(self):
            started = time.time_ns()
            parts = urlsplit(self.path)
            path = parts.path
            status, reason, body, content_type = 200, "snapshot", b"", "application/octet-stream"
            if self.command not in ("GET", "HEAD"):
                status, reason, body = 405, "method", b"Method unavailable\n"
            elif parts.query or parts.fragment or "%" in path or "\\" in path \
                    or ".." in path.split("/") or path not in routes:
                status, reason, body = 404, "not-admitted", b"Object absent from this snapshot\n"
            else:
                body, content_type = routes[path]
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            full_length = len(body)
            response_headers = {}
            if status == 200 and self.headers.get("If-None-Match") == etag:
                status, body = 304, b""
            elif status == 200 and self.headers.get("Range"):
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers["Range"])
                if match:
                    first = int(match[1])
                    last = min(int(match[2]) if match[2] else full_length - 1, full_length - 1)
                    if first <= last and first < full_length:
                        status, body = 206, body[first:last + 1]
                        response_headers["Content-Range"] = f"bytes {first}-{last}/{full_length}"
                    else:
                        status, body = 416, b""
                        response_headers["Content-Range"] = f"bytes */{full_length}"
                else:
                    status, body = 416, b""
                    response_headers["Content-Range"] = f"bytes */{full_length}"
            self.send_response_only(status)
            self.send_header("Content-Type", content_type)
            if status != 304:
                self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, max-age=31536000, immutable"
                             if status in (200, 206, 304) else "no-store")
            for name, value in response_headers.items():
                self.send_header(name, value)
            self.end_headers()
            delivered = self.command != "HEAD" and status != 304
            if delivered:
                self.wfile.write(body)
            event = {"tenant": tenant, "start_utc_ns": started, "end_utc_ns": time.time_ns(),
                     "method": self.command, "path": self.path, "status": status,
                     "reason": reason, "body_bytes": len(body) if delivered else 0,
                     "body_sha256": hashlib.sha256(body if delivered else b"").hexdigest(),
                     "marker": self.headers.get("User-Agent"), "upstream_requests": 0}
            with lock:
                with log_path.open("a") as stream:
                    stream.write(json.dumps(event) + "\n")

        do_GET = serve
        do_HEAD = serve
        do_POST = serve
        do_PUT = serve
        do_DELETE = serve

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=HERE / "data/manifest.json")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    routes, manifest = catalog(args.manifest)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(routes, args.log, args.tenant))
    print(json.dumps({"ready": True, "tenant": args.tenant, "port": server.server_port,
                      "routes": len(routes), "preloaded_bytes": manifest["total_bytes"],
                      "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest()}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
