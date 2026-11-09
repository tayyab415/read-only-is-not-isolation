"""Local config, identity, and fixture checks. No cache binary verdict."""
from collections import Counter
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from contextlib import redirect_stdout
import io

import configuration
import origin
import plan
import store_id


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.origins = {"192.0.2.1": "alice", "192.0.2.2": "bob"}

    def test_unknown_client_does_not_get_a_trusted_store_id(self):
        for line in ("http://p7-test-origin:8000/missing/20/x 192.0.2.250",
                     "http://p7-test-origin:8000/missing/20/x -",
                     "http://unowned.invalid:8000/missing/20/x 192.0.2.1"):
            with self.assertRaises(ValueError):
                store_id.store_id(line, self.origins)

    def test_store_id_separates_actors_and_preserves_exact_query_identity(self):
        url = "http://p7-test-origin:8000/missing/20/x"
        a = store_id.store_id(url + " 192.0.2.1", self.origins)
        b = store_id.store_id(url + " 192.0.2.2", self.origins)
        changed = store_id.store_id(url + "?q=1 192.0.2.1", self.origins)
        self.assertNotEqual(a["store_id"], b["store_id"])
        self.assertNotEqual(a["store_id"], changed["store_id"])
        self.assertEqual(a["actor"], "alice")

    def test_helper_failure_is_recorded_and_never_emits_err_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = folder / "origins.json"
            config.write_text(json.dumps(self.origins))
            log = folder / "helper.jsonl"
            result = subprocess.run([sys.executable, str(Path(store_id.__file__)), str(config), str(log)],
                input="http://p7-test-origin:8000/missing/20/x -\n", text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "BH message=trusted_mapping_failed\n")
            self.assertNotIn("ERR", result.stdout)
            self.assertEqual(json.loads(log.read_text())["failure_type"], "ValueError")

    def test_generated_key_and_disabled_modes_are_explicit(self):
        nginx = configuration.nginx("tenant-keyed", self.origins, "p7-test-origin")
        self.assertIn('proxy_cache_key "p7:$tenant:', nginx)
        self.assertIn("deny all;", nginx)
        self.assertNotIn("if (", nginx)
        self.assertIn("proxy_cache off;", configuration.nginx("disabled", self.origins, "p7-test-origin"))
        squid = configuration.squid("tenant-keyed", self.origins, "p7-test-origin")
        self.assertIn('store_id_extras "%>a"', squid)
        self.assertIn("negative_ttl 60 seconds", squid)
        self.assertIn("http_access deny all", squid)
        self.assertIn("cache deny all", configuration.squid("disabled", self.origins, "p7-test-origin"))

    def test_unowned_or_invalid_runtime_inputs_are_rejected(self):
        for product in configuration.PRODUCTS:
            with self.assertRaises(ValueError):
                configuration.validate(product, "shared", {"192.0.2.1": "unknown"}, "p7-test-origin")
            with self.assertRaises(ValueError):
                configuration.validate(product, "shared", self.origins, "external.example.com")

    def test_every_primary_cohort_is_balanced_and_uses_fresh_paths(self):
        paths = []
        for product in plan.PRODUCTS:
            for delay in plan.DELAYS:
                for arm, split in [("shared", "calibration")] + [(arm, "holdout") for arm in (*plan.ARMS, "no-sender")]:
                    rows = plan.schedule(product, delay, arm, split)
                    self.assertEqual(Counter(row["label"] for row in rows), {0: len(rows) // 2, 1: len(rows) // 2})
                    self.assertTrue(all(row["sender"] != row["receiver"] for row in rows))
                    self.assertTrue(all(not row["active"] for row in rows) if arm == "no-sender" else True)
                    paths.extend(row["path"] for row in rows)
        self.assertEqual(len(paths), 624)
        self.assertEqual(len(set(paths)), 624)
        self.assertEqual(plan.planned_primary_requests()["http_requests"], 904)

    def test_real_origin_preserves_missing_bytes_and_ignores_range_conditions(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), origin.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with redirect_stdout(io.StringIO()):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                started = time.monotonic()
                connection.request("GET", "/missing/20/local-canary", headers={
                    "Range": "bytes=0-1", "If-None-Match": "arbitrary"})
                response = connection.getresponse()
                body = response.read()
                self.assertEqual(response.status, 404)
                self.assertEqual(body, origin.MISSING)
                self.assertGreaterEqual(time.monotonic() - started, .02)
                self.assertIsNone(response.getheader("Cache-Control"))
                self.assertIsNone(response.getheader("Expires"))
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
