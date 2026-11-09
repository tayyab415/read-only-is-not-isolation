"""Local functional route checks. No Linux or cross-tenant verdict."""
import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest

import adapter
import client

P3 = Path(__file__).resolve().parent.parent / "P3-mirror"


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.body = b"approved wheel bytes"
        digest = hashlib.sha256(self.body).hexdigest()
        self.entry = {"filename": "example-1-py3-none-any.whl", "sha256": digest,
                      "bytes": len(self.body), "canonical": "example", "version": "1"}
        self.file_route = f"/files/{digest}/{self.entry['filename']}"
        self.routes = {self.file_route: (self.body, "application/octet-stream"),
                       "/simple/": (b'<a href="/simple/example/">example</a>', "text/html"),
                       "/simple/example/": (
                           f'<a href="{self.file_route}#sha256={digest}">file</a>'.encode(), "text/html")}
        self.backend_calls, self.events, self.servers = [], [], []
        owner = self

        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                owner.backend_calls.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                            "marker": self.headers.get("User-Agent")})
                self.send_response(200)
                self.send_header("Content-Length", str(len(owner.body)))
                self.end_headers()
                self.wfile.write(owner.body)

        backend = self.start(Backend)
        self.config = {"mode": "mutable", "actor": "alice", "run_id": "p3u-test",
                       "origin_binding": {"127.0.0.1": "owned-alice"},
                       "tasks": ["u00-alice-example"], "backend_host": "127.0.0.1",
                       "backend_port": backend.server_port, "backend_prefix": "/artifactory/owned-alice",
                       "user": "owned-alice", "password": "private-test"}
        self.front = self.start(adapter.handler(self.config, self.routes, {"entries": [self.entry]}, events=self.events))

    def start(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    def tearDown(self):
        for server, thread in reversed(self.servers):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def request(self, suffix, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.front.server_port, timeout=2)
        prior = len(self.events)
        connection.request(method, suffix, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        deadline = time.monotonic() + 1
        while len(self.events) <= prior and time.monotonic() < deadline:
            time.sleep(.002)
        return result

    def test_index_is_local_and_wheel_bytes_come_from_the_backend(self):
        prefix = "/task/u00-alice-example"
        status, body, _ = self.request(prefix + "/simple/example/")
        self.assertEqual(status, 200)
        self.assertIn((prefix + self.file_route).encode(), body)
        self.assertEqual(self.backend_calls, [])
        self.assertEqual(self.request(prefix + self.file_route)[:2], (200, self.body))
        event, forwarded = self.events[-1], self.backend_calls[-1]
        self.assertEqual(forwarded["path"], "/artifactory/owned-alice" + self.file_route)
        self.assertEqual(forwarded["marker"], event["marker"])
        self.assertEqual(forwarded["authorization"], "Basic " + base64.b64encode(b"owned-alice:private-test").decode())
        self.assertNotIn("private-test", json.dumps(self.events))

    def test_unknown_task_metadata_path_queries_and_writes_do_not_forward(self):
        for path, method in [("/task/u00-bob-example" + self.file_route, "GET"),
                             ("/task/u00-alice-example/api/storage/foo", "GET"),
                             ("/task/u00-alice-example" + self.file_route + "?stats", "GET"),
                             ("/task/u00-alice-example" + self.file_route, "PUT"),
                             ("/task/u00-alice-example/%2e%2e/files/x", "GET")]:
            self.assertIn(self.request(path, method)[0], (404, 405))
        self.assertEqual(self.backend_calls, [])

    def test_client_authorization_and_unbound_origins_are_rejected(self):
        path = "/task/u00-alice-example" + self.file_route
        self.assertEqual(self.request(path, headers={"Authorization": "Basic copied"})[0], 403)
        self.config["origin_binding"] = {"192.0.2.200": "owned-alice"}
        self.assertEqual(self.request(path)[0], 403)
        self.assertEqual(self.backend_calls, [])

    def test_changed_backend_content_fails_closed(self):
        self.body = b"altered"
        self.assertEqual(self.request("/task/u00-alice-example" + self.file_route)[0], 502)
        self.assertEqual(self.events[-1]["reason"], "backend-integrity")
        self.assertNotEqual(self.events[-1]["backend_body_sha256"], self.entry["sha256"])

    def test_invalid_task_binding_is_rejected_before_listening(self):
        bad = {**self.config, "tasks": ["u00-bob-example"]}
        with self.assertRaises(ValueError):
            adapter.handler(bad, self.routes, {"entries": [self.entry]}, events=[])

    def test_report_must_match_tenant_catalog_version_and_digest(self):
        config = {"host": "owned-adapter", "port": 8000, "task_id": "u00-alice-example"}
        report = {"install": [{"metadata": {"name": "example", "version": "1"},
                              "download_info": {"url": "http://owned-adapter:8000/task/u00-alice-example" + self.file_route,
                                "archive_info": {"hashes": {"sha256": self.entry["sha256"]}}}}]}
        self.assertTrue(client.report_check(report, {"entries": [self.entry]}, config))
        changed = copy.deepcopy(report)
        changed["install"][0]["download_info"]["url"] = "https://example.invalid/file.whl"
        self.assertFalse(client.report_check(changed, {"entries": [self.entry]}, config))
        changed = copy.deepcopy(report)
        changed["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = "wrong"
        self.assertFalse(client.report_check(changed, {"entries": [self.entry]}, config))
        changed = copy.deepcopy(report)
        changed["install"][0]["metadata"]["version"] = "2"
        self.assertFalse(client.report_check(changed, {"entries": [self.entry]}, config))

    def test_real_fresh_pip_install_uses_the_common_route_and_removes_temp_files(self):
        routes, manifest = adapter.load_catalog(P3 / "mirror.py", P3 / "data/manifest.json")
        mirror = importlib.util.spec_from_file_location("utility_test_mirror", P3 / "mirror.py")
        module = importlib.util.module_from_spec(mirror)
        mirror.loader.exec_module(module)
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            backend = self.start(module.handler(routes, Path(temporary) / "backend.jsonl", "local-check"))
            config = {**self.config, "mode": "immutable", "backend_prefix": "",
                      "backend_port": backend.server_port, "tasks": ["u00-alice-packaging"]}
            frontend = self.start(adapter.handler(config, routes, manifest, events=self.events))
            task = {"task_id": "u00-alice-packaging", "actor": "alice", "requirement": "packaging==24.2",
                    "host": "127.0.0.1", "port": frontend.server_port, "manifest": manifest,
                    "check_source_text": (P3 / "utility.py").read_text(),
                    "check_source_sha256": hashlib.sha256((P3 / "utility.py").read_bytes()).hexdigest()}
            completed = subprocess.run([sys.executable, str(Path(client.__file__))],
                                       input=json.dumps(task), text=True, capture_output=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertTrue(result["passed"], result.get("pip_output"))
            self.assertTrue(result["temporary_files_removed"])
            self.assertEqual(result["function_exit_code"], 0)
            self.assertTrue(result["check_source_removed"])
            self.assertEqual(result["check_source_sha256"], task["check_source_sha256"])
            self.assertTrue(any(event["backend_target"] for event in self.events))


if __name__ == "__main__":
    unittest.main()
