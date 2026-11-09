"""Check bounded concurrency and the receiver record contract locally."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

import worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        owner = self
        self.active = self.peak = 0
        self.lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                with owner.lock:
                    owner.active += 1
                    owner.peak = max(owner.peak, owner.active)
                time.sleep(0.01)
                body = json.dumps({"method": self.command}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Range", "response-value")
                self.end_headers()
                self.wfile.write(body)
                with owner.lock:
                    owner.active -= 1

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        class FixtureServer(ThreadingHTTPServer):
            request_queue_size = 128

        self.server = FixtureServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = {"host": "127.0.0.1", "port": self.server.server_port}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def job(self, index, offset=0):
        return {"method": "GET", "path": "/fixture", "marker": f"p2/test/{index}", "offset_s": offset}

    def run_jobs(self, jobs):
        with ThreadPoolExecutor(max_workers=8) as executor:
            return worker.run_jobs(self.config, jobs, "Basic private-test-token", executor)

    def test_concurrent_work_is_bounded_and_all_results_are_retained(self):
        jobs = [self.job(index) for index in range(32)]
        records = self.run_jobs(jobs)
        self.assertEqual([row["marker"] for row in records], [row["marker"] for row in jobs])
        self.assertTrue(all(row["status"] == 200 for row in records))
        self.assertLessEqual(self.peak, 8)
        self.assertGreater(self.peak, 1)

    def test_request_and_response_headers_do_not_overwrite_each_other(self):
        job = self.job(0)
        job["headers"] = {"Range": "bytes=0-0"}
        record = self.run_jobs([job])[0]
        self.assertEqual(record["request_headers"], {"Range": "bytes=0-0"})
        self.assertEqual(dict(record["headers"])["Range"], "response-value")

    def test_credentials_are_absent_from_exported_record(self):
        record = self.run_jobs([self.job(0)])[0]
        self.assertNotIn("private-test-token", json.dumps(record))
        self.assertNotIn("Authorization", json.dumps(record))

    def test_scheduled_offset_and_local_duration_are_separate(self):
        records = self.run_jobs([self.job(0), self.job(1, 0.08)])
        self.assertGreaterEqual(records[1]["start_utc_ns"] - records[0]["start_utc_ns"], 60_000_000)
        self.assertLess(records[1]["elapsed_ms"], 70)
        self.assertGreaterEqual(records[1]["start_lag_ms"], 0)

    def test_failed_request_stays_in_the_returned_records(self):
        config = {"host": "127.0.0.1", "port": 0}
        with ThreadPoolExecutor(max_workers=8) as executor:
            record = worker.run_jobs(config, [self.job(0)], "Basic private-test-token", executor)[0]
        self.assertIsNone(record["status"])
        self.assertIn("error", record)
        self.assertEqual(record["marker"], "p2/test/0")
        self.assertGreaterEqual(record["elapsed_ms"], 0)

    def test_extra_requests_are_rejected_before_dispatch(self):
        with self.assertRaisesRegex(ValueError, "request count"):
            worker.validate_jobs([self.job(index) for index in range(129)])

    def test_authorization_override_and_header_injection_are_rejected(self):
        job = self.job(0)
        job["headers"] = {"Authorization": "untrusted"}
        with self.assertRaisesRegex(ValueError, "header outside"):
            worker.validate_jobs([job])
        job["headers"] = {"Range": "bytes=0-0\r\nInjected: value"}
        with self.assertRaisesRegex(ValueError, "invalid experimental header"):
            worker.validate_jobs([job])

    def test_foreign_urls_writes_and_path_traversal_are_rejected(self):
        for change in [{"path": "https://example.invalid/"},
                       {"path": "//example.invalid/"},
                       {"path": "/../private"}, {"path": "/%2e%2e/private"},
                       {"path": "/fixture#hidden"}, {"method": "PUT"}]:
            with self.assertRaises(ValueError):
                worker.validate_jobs([{**self.job(0), **change}])

    def test_nonfinite_and_out_of_window_offsets_are_rejected(self):
        for offset in [float("nan"), float("inf"), -1, 4, True]:
            with self.assertRaisesRegex(ValueError, "offset"):
                worker.validate_jobs([self.job(0, offset)])

    def test_common_epoch_is_respected(self):
        epoch = time.time_ns() + 80_000_000
        with ThreadPoolExecutor(max_workers=8) as executor:
            record = worker.run_jobs(self.config, [self.job(0)], "Basic private-test-token",
                                     executor, epoch)[0]
        self.assertGreaterEqual(record["start_utc_ns"], epoch - 2_000_000)
        self.assertEqual(record["schedule_epoch_utc_ns"], epoch)

    def test_invalid_or_stale_common_epoch_is_rejected(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            for epoch in [True, time.time_ns() - 2_000_000_000,
                          time.time_ns() + 3_000_000_000]:
                with self.assertRaisesRegex(ValueError, "epoch"):
                    worker.run_jobs(self.config, [self.job(0)], "Basic private-test-token",
                                    executor, epoch)

    def test_idle_execution_flag_sends_no_http(self):
        # Use an allowed past epoch so the fixed idle wait is only about one second.
        epoch = time.time_ns() - 900_000_000
        with ThreadPoolExecutor(max_workers=8) as executor:
            rows = worker.run_jobs(self.config, [self.job(0)], "Basic private-test-token",
                                   executor, epoch, execute_http=0)
        self.assertEqual(rows, [])
        self.assertEqual(self.peak, 0)

    def test_private_execution_flag_is_strictly_binary(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            for flag in [True, "0", 2]:
                with self.assertRaisesRegex(ValueError, "execution flag"):
                    worker.run_jobs(self.config, [self.job(0)], "Basic private-test-token",
                                    executor, execute_http=flag)


if __name__ == "__main__":
    unittest.main(verbosity=2)
