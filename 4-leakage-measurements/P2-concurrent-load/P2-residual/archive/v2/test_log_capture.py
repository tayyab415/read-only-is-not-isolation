"""Exercise the native archive shell contract with real gzip/plain fixtures."""
import gzip
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import service


class NativeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "archived").mkdir()
        names = [
            "artifactory-request.log", "router-request.log",
            "archived/artifactory-request.2026.log",
            "archived/artifactory-request.2026.log.gz",
            "archived/router-request-2026.log",
            "archived/router-request-2026.log.gz",
            "archived/router-request.2025.log.gz",
        ]
        self.expected = []
        for index, name in enumerate(names):
            line = f"native-row-{index} p1/p2-s1-c-separate/marker-{index}"
            self.expected.append(line)
            data = (line + "\nunrelated row\n").encode()
            target = self.root / name
            if name.endswith(".gz"):
                target.write_bytes(gzip.compress(data))
            else:
                target.write_bytes(data)

    def tearDown(self):
        self.temp.cleanup()

    def run_shell(self, command):
        self.assertEqual(command[:5], ["docker", "exec", "art-a", "sh", "-c"])
        text = command[5].replace(
            "/var/opt/jfrog/artifactory/log", str(self.root))
        return subprocess.check_output(["sh", "-c", text], text=True)

    def test_all_native_naming_and_compression_variants(self):
        with patch.object(service.old, "run_ok", side_effect=self.run_shell):
            self.assertEqual(set(service.server_log_lines("p2-s1-c-separate")),
                             set(self.expected))

    def test_corrupt_archive_stops_capture(self):
        (self.root / "archived/router-request-2026.log.gz").write_bytes(b"invalid")
        with patch.object(service.old, "run_ok", side_effect=self.run_shell):
            with self.assertRaises(subprocess.CalledProcessError):
                service.server_log_lines("p2-s1-c-separate")


if __name__ == "__main__":
    unittest.main()
