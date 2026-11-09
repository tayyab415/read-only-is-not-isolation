import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import threading
import time
import unittest

import gateway


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.forwarded, self.events = [], []
        owner = self

        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                owner.forwarded.append((self.path, dict(self.headers)))
                self.send_response(200)
                self.send_header("Content-Length", "7")
                self.end_headers()
                self.wfile.write(b"fixture")

            def do_HEAD(self):
                owner.forwarded.append((self.path, dict(self.headers)))
                self.send_response(200)
                self.send_header("Content-Length", "7")
                self.end_headers()

        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        config = {"origin_binding": {"127.0.0.1": "owned-alice"},
                  "repos": {"owned": {"block_storage": True}},
                  "backend_host": "127.0.0.1", "backend_port": self.backend.server_port}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.handler(config, self.events))
        self.threads = [threading.Thread(target=server.serve_forever, daemon=True)
                        for server in (self.backend, self.server)]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server in (self.server, self.backend):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)

    def request(self, path="/artifactory/owned/pkg.bin", method="GET", user="owned-alice", headers=None):
        encoded = base64.b64encode((user + ":private-token").encode()).decode()
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        client.request(method, path, headers={"Authorization": "Basic " + encoded,
                                             "User-Agent": "p1/p2-test/owned", **(headers or {})})
        response = client.getresponse()
        result = response.status, response.read()
        client.close()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not self.events:
            time.sleep(0.005)
        return result

    def test_full_query_and_wire_headers_are_preserved(self):
        path = "/artifactory/owned/pkg.bin?probe=owned"
        result = self.request(path, headers={"Range": "bytes=0-0", "If-None-Match": '"fixture"'})
        self.assertEqual(result, (200, b"fixture"))
        self.assertEqual(self.forwarded[0][0], path)
        self.assertEqual(self.events[0]["path"], path)
        self.assertEqual(dict(self.events[0]["request_headers"]),
                         {"range": "bytes=0-0", "if-none-match": '"fixture"'})
        self.assertEqual(self.events[0]["body_sha256"], hashlib.sha256(b"fixture").hexdigest())
        self.assertNotIn("private-token", json.dumps(self.events))

    def test_storage_block_has_no_backend_action(self):
        self.assertEqual(self.request("/artifactory/api/storage/owned-cache/pkg.bin?stats")[0], 403)
        self.assertEqual(self.forwarded, [])
        self.assertEqual(self.events[0]["policy_reason"], "storage")

    def test_borrowed_identity_is_rejected_before_backend(self):
        self.assertEqual(self.request(user="owned-bob")[0], 403)
        self.assertEqual(self.forwarded, [])
        self.assertIsNone(self.events[0]["principal"])

    def test_writes_foreign_scope_and_encoded_traversal_fail(self):
        self.assertEqual(self.request(method="PUT")[0], 405)
        self.assertEqual(self.request("/artifactory/foreign/pkg.bin")[0], 403)
        self.assertEqual(self.request("/artifactory/owned/%2e%2e/private")[0], 403)
        self.assertEqual(self.forwarded, [])

    def test_head_delivers_no_body_and_has_a_matching_hash(self):
        self.assertEqual(self.request(method="HEAD"), (200, b""))
        self.assertEqual(self.events[0]["body_bytes"], 0)
        self.assertEqual(self.events[0]["body_sha256"], hashlib.sha256(b"").hexdigest())


if __name__ == "__main__":
    unittest.main()
