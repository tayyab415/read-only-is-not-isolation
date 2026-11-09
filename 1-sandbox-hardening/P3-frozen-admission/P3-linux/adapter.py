"""Common utility index adapter, with wheel bytes supplied by the backend."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlsplit

TASK = re.compile(r"u\d{2}-(alice|bob)-[a-z0-9_.-]+")
HEADERS = ("Content-Type", "ETag", "Last-Modified", "Content-Range", "Accept-Ranges")


def load_catalog(source, manifest):
    spec = importlib.util.spec_from_file_location("p3_utility_catalog", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.catalog(manifest)


def validate_config(config):
    if config["mode"] not in {"mutable", "immutable"} or config["actor"] not in {"alice", "bob"}:
        raise ValueError("Unknown utility route")
    if not config["origin_binding"] or not config["tasks"] or not 1 <= config["backend_port"] <= 65535:
        raise ValueError("Incomplete utility route")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", config["run_id"]):
        raise ValueError("Invalid owned run identity")
    for task in config["tasks"]:
        matched = TASK.fullmatch(task)
        if not matched or matched[1] != config["actor"]:
            raise ValueError("Task leaves its tenant")
    for value in (config["backend_host"], config.get("backend_prefix", "")):
        if not isinstance(value, str) or any(c in value for c in "\r\n"):
            raise ValueError("Invalid backend route")
    if config["mode"] == "mutable" and not all(
            isinstance(config.get(key), str) and config[key] for key in ("user", "password")):
        raise ValueError("Mutable route has no scoped credential")


def handler(config, routes, manifest, log_path=None, events=None):
    validate_config(config)
    lock, sequence = threading.Lock(), 0
    files = {"/files/" + row["sha256"] + "/" + row["filename"]: row for row in manifest["entries"]}
    if set(files) - set(routes):
        raise ValueError("Catalog file routes differ from the admission manifest")
    indices = {path: value for path, value in routes.items() if path.startswith("/simple/")}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def serve(self):
            nonlocal sequence
            started = time.time_ns()
            with lock:
                sequence += 1
                marker = f"p1/{config['run_id']}/utility/{config['actor']}/u{sequence:06d}"
            parts = urlsplit(self.path)
            components = parts.path.split("/", 3)
            task = components[2] if len(components) == 4 and components[1] == "task" else None
            route = "/" + components[3] if task is not None else ""
            principal = config["origin_binding"].get(self.client_address[0])
            reason, status, body, headers = None, 200, b"", []
            forwarded, backend_status, backend_body_sha = None, None, None
            if principal is None or self.headers.get("Authorization") is not None:
                reason, status = "origin", 403
            elif self.command not in {"GET", "HEAD"}:
                reason, status = "method", 405
            elif parts.query or parts.fragment or "%" in parts.path or "\\" in parts.path \
                    or ".." in parts.path.split("/") or task not in config["tasks"]:
                reason, status = "task-scope", 404
            elif route in indices:
                body, content_type = indices[route]
                body = body.replace(b'href="/', f'href="/task/{task}/'.encode())
                headers = [("Content-Type", content_type), ("ETag", '"' + hashlib.sha256(body).hexdigest() + '"')]
            elif route in files:
                forwarded = config.get("backend_prefix", "") + route
                connection = http.client.HTTPConnection(config["backend_host"], config["backend_port"], timeout=30)
                outbound = {"User-Agent": marker}
                if config["mode"] == "mutable":
                    outbound["Authorization"] = "Basic " + base64.b64encode(
                        (config["user"] + ":" + config["password"]).encode()).decode()
                try:
                    connection.request(self.command, forwarded, headers=outbound)
                    response = connection.getresponse()
                    backend_status = status = response.status
                    body = response.read(files[route]["bytes"] + 1)
                    backend_body_sha = hashlib.sha256(body).hexdigest()
                    headers = [(name, response.getheader(name)) for name in HEADERS
                               if response.getheader(name) is not None]
                    if self.command == "HEAD":
                        headers.append(("Content-Length", response.getheader("Content-Length", "0")))
                    elif status == 200 and (backend_body_sha != files[route]["sha256"]
                                            or len(body) != files[route]["bytes"]):
                        reason, status, body, headers = "backend-integrity", 502, b"Backend content differs\n", []
                except Exception as exc:
                    reason, status, body, headers = "backend-" + type(exc).__name__, 502, b"Backend unavailable\n", []
                finally:
                    connection.close()
            else:
                reason, status = "not-admitted", 404
            if reason and not body:
                body = b"Utility route unavailable\n"
            try:
                self.send_response_only(status)
                for name, value in headers:
                    self.send_header(name, value)
                if not any(name.lower() == "content-length" for name, _ in headers):
                    self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                delivered = body if self.command != "HEAD" else b""
                if delivered:
                    self.wfile.write(delivered)
            finally:
                delivered = body if self.command != "HEAD" else b""
                event = {"start_utc_ns": started, "end_utc_ns": time.time_ns(), "task_id": task,
                         "actor": config["actor"], "principal": principal,
                         "client_ip": self.client_address[0], "method": self.command,
                         "path": self.path, "status": status, "reason": reason,
                         "marker": marker, "backend_target": forwarded, "backend_status": backend_status,
                         "backend_body_sha256": backend_body_sha,
                         "body_bytes": len(delivered), "body_sha256": hashlib.sha256(delivered).hexdigest(),
                         "credential_supplied_to_client": False, "adapter_edition": 1}
                with lock:
                    if events is not None:
                        events.append(event)
                    else:
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
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(sys.stdin.readline())
    for path, field in [(args.source, "mirror_source_sha256"), (args.manifest, "manifest_sha256")]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != config[field]:
            raise RuntimeError("Frozen utility input differs")
    routes, manifest = load_catalog(args.source, args.manifest)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.touch(exist_ok=False)
    server = ThreadingHTTPServer(("0.0.0.0", 8000), handler(config, routes, manifest, args.log))
    # EOF or an explicit line stops the owned server. No credential is printed.
    threading.Thread(target=lambda: (sys.stdin.readline(), server.shutdown()), daemon=True).start()
    print(json.dumps({"ready": True, "pid": os.getpid(), "adapter_edition": 1,
                      "mode": config["mode"], "actor": config["actor"],
                      "manifest_sha256": config["manifest_sha256"]}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
