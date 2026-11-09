"""Functional counterexamples for the immutable serving implementation."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

import mirror


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.content = b"verified package bytes\n"
        self.digest = hashlib.sha256(self.content).hexdigest()
        (self.root / "wheel.whl").write_bytes(self.content)
        self.manifest = self.root / "manifest.json"
        self.entry = {"filename": "test_pkg-1.0-py3-none-any.whl", "path": "wheel.whl",
                      "canonical": "test-pkg", "sha256": self.digest,
                      "bytes": len(self.content), "requires_python": ">=3.10"}
        self.manifest.write_text(json.dumps({"entries": [self.entry]}))
        self.routes, _ = mirror.catalog(self.manifest)
        self.file_route = next(path for path in self.routes if path.startswith("/files/"))
        self.log = self.root / "requests.jsonl"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), mirror.handler(self.routes, self.log, "self"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.getheaders(), response.read()
        connection.close()
        return result

    def test_changed_wheel_fails_before_serving(self):
        (self.root / "wheel.whl").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            mirror.catalog(self.manifest)

    def test_manifest_cannot_name_file_outside_content_directory(self):
        self.entry["path"] = "../outside.whl"
        self.manifest.write_text(json.dumps({"entries": [self.entry]}))
        with self.assertRaisesRegex(ValueError, "escapes"):
            mirror.catalog(self.manifest)

    def test_missing_stats_and_traversal_requests_return_fixed_local_failure(self):
        for path in ["/missing", "/api/storage/test?stats", "/../manifest.json",
                     "/%2e%2e/manifest.json", "/simple/test-pkg/?stats"]:
            status, _, body = self.request("GET", path)
            self.assertEqual(status, 404)
            self.assertEqual(body, b"Object absent from this snapshot\n")
        events = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(all(row["upstream_requests"] == 0 for row in events))

    def test_peer_read_history_does_not_change_application_response(self):
        before = self.request("GET", self.file_route)
        for _ in range(15):
            self.request("GET", self.file_route)
            self.request("GET", "/missing")
        after = self.request("GET", self.file_route)
        self.assertEqual(before, after)
        self.assertEqual(after[2], self.content)
        self.assertEqual(hashlib.sha256((self.root / "wheel.whl").read_bytes()).hexdigest(), self.digest)

    def test_head_and_conditional_get_preserve_representation_etag(self):
        status, headers, body = self.request("GET", self.file_route)
        etag = dict(headers)["ETag"]
        head = self.request("HEAD", self.file_route)
        self.assertEqual(head[0], 200)
        self.assertEqual(dict(head[1])["Content-Length"], str(len(body)))
        self.assertEqual(head[2], b"")
        conditional = self.request("GET", self.file_route, {"If-None-Match": etag})
        self.assertEqual(conditional[0], 304)
        self.assertEqual(conditional[2], b"")
        self.assertNotIn("Content-Length", dict(conditional[1]))
        self.assertEqual(dict(conditional[1])["ETag"], etag)

    def test_range_get_returns_only_requested_bytes(self):
        status, headers, body = self.request("GET", self.file_route, {"Range": "bytes=0-3"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.content[:4])
        self.assertEqual(dict(headers)["Content-Range"], f"bytes 0-3/{len(self.content)}")
        self.assertEqual(self.request("GET", self.file_route, {"Range": "bytes=999-1000"})[0], 416)

    def test_write_method_is_rejected(self):
        self.assertEqual(self.request("PUT", self.file_route)[0], 405)
        self.assertEqual(self.request("GET", self.file_route)[2], self.content)

    def test_simple_index_has_frozen_hash_and_python_requirement(self):
        status, _, body = self.request("GET", "/simple/test-pkg/")
        self.assertEqual(status, 200)
        self.assertIn(f"#sha256={self.digest}".encode(), body)
        self.assertIn(b'data-requires-python="&gt;=3.10"', body)
        self.assertIn(self.entry["filename"].encode(), body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
