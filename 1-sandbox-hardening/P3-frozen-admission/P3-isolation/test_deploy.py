import base64
import hashlib
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base_log, self.wire_log = self.root / "base.jsonl", self.root / "wire.jsonl"
        module = deploy.load_mirror(deploy.HERE.parent / "P3-mirror" / "mirror.py")
        self.routes = {"/fixture/pkg-00.bin": (deploy.PAYLOAD, "application/octet-stream"),
                       "/simple/": (b"<a>fixed catalog</a>", "text/html")}
        self.origins = {"127.0.0.1": "owned-alice"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), deploy.traced_handler(
            module, self.routes, self.base_log, self.wire_log, "alice", self.origins))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        client.request(method, path, headers={"User-Agent": "p1/p2-test/owned", **(headers or {})})
        response = client.getresponse()
        result = response.status, response.read(), response.getheaders()
        client.close()
        return result

    def records(self, path, count=1):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if path.exists():
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                if len(rows) >= count:
                    return rows
            time.sleep(0.005)
        self.fail("Expected trusted records were not flushed")

    def test_fixture_bytes_and_range_match_the_controlled_payload(self):
        status, body, _ = self.request("/fixture/pkg-00.bin")
        self.assertEqual((status, body), (200, deploy.PAYLOAD))
        status, body, _ = self.request("/fixture/pkg-00.bin", headers={"Range": "bytes=0-0"})
        self.assertEqual((status, body), (206, deploy.PAYLOAD[:1]))
        base = self.records(self.base_log, 2)
        self.assertEqual(base[0]["body_sha256"], hashlib.sha256(deploy.PAYLOAD).hexdigest())

    def test_borrowed_username_and_unknown_origin_are_rejected(self):
        encoded = base64.b64encode(b"owned-bob:ignored").decode()
        self.assertEqual(self.request("/fixture/pkg-00.bin",
                                      headers={"Authorization": "Basic " + encoded})[0], 403)
        self.origins.clear()
        self.assertEqual(self.request("/fixture/pkg-00.bin")[0], 403)
        rows = self.records(self.wire_log, 2)
        self.assertTrue(all(not row["origin_permitted"] for row in rows))
        self.assertFalse(self.base_log.exists())

    def test_origin_route_works_without_sandbox_credentials(self):
        self.assertEqual(self.request("/fixture/pkg-00.bin")[0], 200)
        row = self.records(self.wire_log)[0]
        self.assertEqual(row["principal"], "owned-alice")
        self.assertEqual(row["identity_source"], "trusted network origin")

    def test_request_headers_are_attested_and_credentials_are_omitted(self):
        encoded = base64.b64encode(b"owned-alice:private").decode()
        self.request("/fixture/pkg-00.bin", headers={
            "Authorization": "Basic " + encoded, "Range": "bytes=0-0"})
        row = self.records(self.wire_log)[0]
        self.assertEqual(row["request_headers"], {"range": "bytes=0-0"})
        self.assertNotIn("private", json.dumps(row))
        self.assertNotIn(encoded, json.dumps(row))
        self.assertEqual(row["status"], 206)

    def test_metadata_logs_and_writes_remain_unavailable(self):
        for path in ["/api/storage/snapshot/pkg-15.bin?stats", "/wire.jsonl", "/../simple/"]:
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/fixture/pkg-00.bin", "PUT")[0], 405)

    def test_application_responses_stay_fixed_after_unrelated_reads(self):
        first = self.request("/simple/")
        for _ in range(8):
            self.request("/fixture/pkg-00.bin")
        self.request("/missing/owned.bin")
        self.assertEqual(self.request("/simple/"), first)


if __name__ == "__main__":
    unittest.main()
