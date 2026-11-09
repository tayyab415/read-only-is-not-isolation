import unittest
import json
import select
import socket
import subprocess
import sys

import preflight


class PreflightTests(unittest.TestCase):
    def test_live_positive_and_denied_negative_controls_are_required(self):
        rows = [{"name": "live", "connected": True, "expected_connected": True},
                {"name": "blocked", "connected": False, "expected_connected": False}]
        self.assertTrue(preflight.check_results(rows)["passed"])
        rows[0]["connected"] = False
        with self.assertRaises(RuntimeError):
            preflight.check_results(rows)

    def test_open_bypass_and_empty_results_cannot_pass(self):
        with self.assertRaises(RuntimeError):
            preflight.check_results([])
        with self.assertRaises(RuntimeError):
            preflight.check_results([{"name": "bypass", "connected": True, "expected_connected": False}])

    def test_listener_accepts_a_live_connection_and_stops_on_stdin(self):
        script = preflight.LISTENER.replace("0.0.0.0", "127.0.0.1").replace("18888", "0")
        process = subprocess.Popen([sys.executable, "-u", "-c", script],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 3)
            self.assertTrue(ready)
            record = json.loads(process.stdout.readline())
            self.assertTrue(record["ready"])
            connection = socket.create_connection(("127.0.0.1", record["port"]), 1)
            connection.close()
            process.stdin.write("stop\n")
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


if __name__ == "__main__":
    unittest.main()
