"""P3 serving edition 2: private-network listener and request attestation.

The earlier utility server and admission manifest remain unchanged. This
edition adds equal-byte controlled fixture routes for P2's timed workload.
"""
import argparse
import base64
import hashlib
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import time

HERE = Path(__file__).resolve().parent
PAYLOAD = b"p1-meter fixture package bytes\n"


def load_mirror(path):
    spec = importlib.util.spec_from_file_location("p3_frozen_mirror", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def traced_handler(module, routes, base_log, wire_log, tenant, origin_binding):
    base = module.handler(routes, base_log, tenant)
    lock = threading.Lock()

    class Handler(base):
        def send_response_only(self, code, message=None):
            self.response_status = code
            super().send_response_only(code, message)

        def serve_traced(self):
            started = time.time_ns()
            expected = origin_binding.get(self.client_address[0])
            claimed = None
            auth = self.headers.get("Authorization")
            if auth:
                try:
                    scheme, encoded = auth.split(" ", 1)
                    if scheme.lower() == "basic":
                        claimed = base64.b64decode(encoded, validate=True).decode().split(":", 1)[0]
                except (ValueError, UnicodeError):
                    pass
            # Origin determines the identity. A Basic username is an optional
            # consistency check, not independent password authentication.
            permitted = expected is not None and (auth is None or claimed == expected)
            error = None
            try:
                if permitted:
                    base.serve(self)
                else:
                    body = b"Origin route unavailable\n"
                    self.send_response_only(403)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
            except Exception as exc:
                error = type(exc).__name__
                raise
            finally:
                event = {"start_utc_ns": started, "end_utc_ns": time.time_ns(),
                         "method": self.command, "path": self.path,
                         "principal": expected if permitted else None,
                         "identity_source": "trusted network origin",
                         "origin_permitted": permitted, "error": error,
                         "status": getattr(self, "response_status", None),
                         "client_ip": self.client_address[0],
                         "marker": self.headers.get("User-Agent"),
                         "request_headers": {name.lower(): self.headers[name]
                                             for name in ("Range", "If-None-Match", "If-Modified-Since")
                                             if name in self.headers}}
                with lock:
                    with wire_log.open("a") as stream:
                        stream.write(json.dumps(event) + "\n")

        do_GET = serve_traced
        do_HEAD = serve_traced
        do_POST = serve_traced
        do_PUT = serve_traced
        do_DELETE = serve_traced

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror-source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--wire-log", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if hashlib.sha256(args.mirror_source.read_bytes()).hexdigest() != config["mirror_source_sha256"] \
            or hashlib.sha256(args.manifest.read_bytes()).hexdigest() != config["manifest_sha256"]:
        raise RuntimeError("Frozen mirror input differs from the deployment configuration")
    if config["queue_size"] not in (5, 128):
        raise ValueError("Unapproved serving queue size")
    module = load_mirror(args.mirror_source)
    routes, manifest = module.catalog(args.manifest)
    for value in range(16):
        routes[f"/fixture/pkg-{value:02d}.bin"] = (PAYLOAD, "application/octet-stream")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.wire_log.parent.mkdir(parents=True, exist_ok=True)

    class Server(ThreadingHTTPServer):
        request_queue_size = config["queue_size"]

    server = Server(("0.0.0.0", args.port), traced_handler(
        module, routes, args.log, args.wire_log, config["tenant"], config["origin_binding"]))
    print(json.dumps({"ready": True, "edition": 2, "tenant": config["tenant"],
                      "routes": len(routes), "queue_size": server.request_queue_size,
                      "manifest_sha256": config["manifest_sha256"],
                      "fixture_sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                      "origin_count": len(config["origin_binding"])}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
