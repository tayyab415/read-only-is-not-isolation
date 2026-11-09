import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import audit
import experiment as e
import participant

HERE = Path(__file__).resolve().parent


class UtilityTests(unittest.TestCase):
    def test_private_file_write_and_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.txt"
            with patch.object(participant, "Path", return_value=path):
                result = participant.file_task()
            self.assertTrue(result["successful"])
            self.assertEqual(path.read_bytes(), e.FILE_BYTES)
            self.assertEqual(result["sha256"], e.digest(e.FILE_BYTES))
            self.assertEqual(result["bytes"], 1024)

    def test_real_http_body_and_two_native_logs_join(self):
        deployment_path = HERE.parent / "P3-isolation/deploy.py"
        spec = importlib.util.spec_from_file_location("p8_test_p3_deployment", deployment_path)
        deployment = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deployment)
        mirror = deployment.load_mirror(HERE.parent / "P3-mirror/mirror.py")
        with tempfile.TemporaryDirectory() as directory:
            base, wire = Path(directory) / "base.jsonl", Path(directory) / "wire.jsonl"
            handler = deployment.traced_handler(
                mirror, {"/fixture/pkg-00.bin": (e.PAYLOAD, "application/octet-stream")},
                base, wire, "bob", {"127.0.0.1": "private-bob"})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            original = http.client.HTTPConnection
            try:
                with patch.object(participant.http.client, "HTTPConnection",
                                  side_effect=lambda host, port, timeout: original(
                                      host, server.server_port, timeout=timeout)):
                    request = participant.http_task({"host": "127.0.0.1"}, "p8/local/opaque/bob/pre")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            self.assertEqual(request["status"], 200)
            self.assertEqual(request["body_sha256"], e.digest(e.PAYLOAD))
            public = {"networks": {"bob": "private-net"}, "users": {"bob": "private-bob"},
                      "boundary": {"containers": {"bob": {"network_addresses": {"private-net": "127.0.0.1"}}}}}
            checks = audit.Checks()
            audit.join_http(request, [json.loads(base.read_text())], [json.loads(wire.read_text())],
                            public, "bob", checks.check, "local")
            self.assertTrue(checks.result()["passed"], checks.result())


if __name__ == "__main__":
    unittest.main()
