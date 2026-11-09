"""Paired dispatch counterexamples without Docker or a live service."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import threading
import time
import unittest

import harness
import plan


class FakeClient:
    def __init__(self, actor, events):
        self.actor, self.events = actor, events

    def start(self, jobs, epoch, execute_http=1):
        self.events.append(("start", self.actor, epoch))
        self.supplied_jobs = jobs
        self.jobs, self.epoch = jobs if execute_http else [], epoch

    def finish(self):
        self.events.append(("finish", self.actor, self.epoch))
        return [{**row, "actor": self.actor, "status": 200,
                 "start_utc_ns": self.epoch + int(row["offset_s"] * 1e9),
                 "schedule_setup_utc_ns": self.epoch - 250_000_000,
                 "end_utc_ns": self.epoch + int(row["offset_s"] * 1e9) + 1_000_000}
                for row in self.jobs]


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.state = {"repositories": {"alice": "owned-alice", "bob": "owned-bob"},
                      "fixture_etag": '"etag"'}
        self.trial = {"label": 1, "recipe": "get", "sender": "alice", "receiver": "bob",
                      "trial_id": "c0000", "token": "deadbeef"}

    def test_both_workers_dispatch_before_either_is_read(self):
        events = []
        clients = {actor: FakeClient(actor, events) for actor in ("alice", "bob")}
        record = harness.run_trial(self.state, "p2-test", "separate", self.trial, clients)
        self.assertEqual([event[0] for event in events], ["start", "start", "finish", "finish"])
        self.assertEqual(len({event[2] for event in events}), 1)
        self.assertEqual(len(record["sender_records"]), 128)
        self.assertEqual(len(record["post"]), 21)

    def test_idle_dispatches_same_two_rpc_operations(self):
        events = []
        clients = {actor: FakeClient(actor, events) for actor in ("alice", "bob")}
        record = harness.run_trial(self.state, "p2-test", "separate",
                                   {**self.trial, "label": 0}, clients)
        self.assertEqual([event[0] for event in events], ["start", "start", "finish", "finish"])
        self.assertEqual(record["sender_records"], [])
        self.assertEqual(len(clients["alice"].supplied_jobs), 128)
        self.assertEqual(len(record["post"]), 21)

    def test_receiver_jobs_are_unaffected_by_sender_result_delay(self):
        events = []

        class SlowSender(FakeClient):
            def finish(self):
                time.sleep(0.01)
                return super().finish()

        clients = {"alice": SlowSender("alice", events), "bob": FakeClient("bob", events)}
        record = harness.run_trial(self.state, "p2-test", "separate", self.trial, clients)
        expected = plan.receiver_jobs(self.state, "p2-test", self.trial)
        self.assertEqual([row["offset_s"] for row in record["post"]],
                         [row["offset_s"] for row in expected])

    def test_failed_requests_remain_and_stop_batch(self):
        events = []

        class BrokenSender(FakeClient):
            def finish(self):
                rows = super().finish()
                rows[0]["status"] = None
                rows[0]["error"] = "TimeoutError"
                return rows

        clients = {"alice": BrokenSender("alice", events), "bob": FakeClient("bob", events)}
        record = harness.run_trial(self.state, "p2-test", "separate", self.trial, clients)
        self.assertTrue(record["batch_must_stop"])
        self.assertEqual(len(record["sender_records"]), 128)
        self.assertEqual(len(record["failed_request_markers"]), 1)

    def test_worker_subprocess_roundtrip_and_scoping(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = harness.Client([sys.executable, "-u", str(harness.HERE / "worker.py")],
                                {"host": "127.0.0.1", "port": server.server_port,
                                 "user": "owned", "password": "private-test", "workers": 8},
                                "alice", self.state)
        try:
            job = {"method": "GET", "path": "/owned-alice/pkg-00.bin", "marker": "p1/p2-test/a",
                   "offset_s": 0, "probe": "test"}
            with self.assertRaises(ValueError):
                client.start([{**job, "path": "/owned-bob/pkg-00.bin"}], time.time_ns())
            client.start([job], time.time_ns() + 50_000_000)
            with self.assertRaises(RuntimeError):
                client.start([job], time.time_ns())
            rows = client.finish()
            self.assertEqual(rows[0]["status"], 200)
            self.assertEqual(rows[0]["path"], job["path"])
            self.assertNotIn("private-test", str(rows))
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertIsNotNone(client.proc.returncode)


if __name__ == "__main__":
    unittest.main()
