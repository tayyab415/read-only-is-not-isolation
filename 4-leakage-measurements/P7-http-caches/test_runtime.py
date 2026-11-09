"""Local dispatcher, parser, and decoder checks; no cache image or VM use."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import analysis
import audit
import plan
from request_guard import RequestGuard
import service


class RuntimeTests(unittest.TestCase):
    def test_import_keeps_the_cache_plan_separate_from_the_reused_p2_plan(self):
        self.assertEqual(Path(plan.__file__).parent.name, "P7-caches")
        self.assertEqual(Path(service.p2.plan.__file__).parent.name, "P2-residual")

    def test_sender_uses_the_same_worker_and_job_except_its_private_execution_bit(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            runtime = service.Runtime("p7-g2-nginx-20-shared-cal", "nginx", "shared", folder,
                                      RequestGuard(folder / "reservations.jsonl"))
            calls = []

            def worker(args, input, timeout):
                job = json.loads(input)
                calls.append((args, job))
                return subprocess.CompletedProcess(args, 0, json.dumps({
                    **job, "start_utc_ns": 1, "end_utc_ns": 2,
                    "status": 404 if job["execute"] else None, "idle": not job["execute"]}), "")

            with patch.object(service.old, "run", side_effect=worker):
                runtime.request("alice", "GET", "/missing/20/test", "p7/test/active", execute=True)
                runtime.request("alice", "GET", "/missing/20/test", "p7/test/idle", execute=False)
            self.assertEqual(calls[0][0], calls[1][0])
            active, idle = calls[0][1], calls[1][1]
            self.assertEqual({key: value for key, value in active.items() if key not in ("marker", "execute")},
                             {key: value for key, value in idle.items() if key not in ("marker", "execute")})
            self.assertEqual(active["execute"], 1)
            self.assertEqual(idle["execute"], 0)
            self.assertEqual(len((folder / "requests.jsonl").read_text().splitlines()), 2)
            self.assertEqual(len((folder / "reservations.jsonl").read_text().splitlines()), 1)

    def test_request_scope_rejection_precedes_reservation_and_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            runtime = service.Runtime("p7-g2-nginx-20-shared-cal", "nginx", "shared", folder,
                                      RequestGuard(folder / "reservations.jsonl"))
            for path in ("http://outside.invalid/", "/missing/20/../escape", "/api/storage/foreign",
                         "/missing/20/test?unexpected"):
                with patch.object(service.old, "run") as dispatch:
                    with self.assertRaises(ValueError):
                        runtime.request("alice", "GET", path, "p7/test/rejected")
                    dispatch.assert_not_called()
            self.assertFalse((folder / "reservations.jsonl").exists())

    def test_unknown_origin_success_is_retained_then_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            runtime = service.Runtime("p7-g2-nginx-20-shared-cal", "nginx", "shared", folder,
                                      RequestGuard(folder / "reservations.jsonl"))

            def worker(args, input, timeout):
                return subprocess.CompletedProcess(args, 0, json.dumps({**json.loads(input), "status": 200}), "")

            with patch.object(service.old, "run", side_effect=worker):
                with self.assertRaisesRegex(RuntimeError, "not rejected"):
                    runtime.rejected_origin_request("p7/test/unknown-origin", "/missing/20/denial")
            self.assertEqual(json.loads((folder / "requests.jsonl").read_text())["status"], 200)
            self.assertEqual(len((folder / "reservations.jsonl").read_text().splitlines()), 1)

    def test_squid_parser_preserves_target_and_native_negative_hit(self):
        text = "1794166800.125 192.0.2.1 GET http://p7-test-origin:8000/missing/20/x 404 TCP_NEGATIVE_HIT 1 p7/test/receiver\n"
        row = audit.native("squid", text)[0]
        self.assertEqual(row["path"], "http://p7-test-origin:8000/missing/20/x")
        self.assertEqual(row["cache_status"], "TCP_NEGATIVE_HIT")
        self.assertEqual(row["request_time_s"], "0.001")
        with self.assertRaises(ValueError):
            audit.native("squid", text.replace(" p7/test/receiver", " unexpected field p7/test/receiver"))

    def test_classifier_uses_only_the_frozen_receiver_time(self):
        trials = [{"label": label, "receiver_request": {"elapsed_ms": 10 if label else 150}}
                  for label in (0, 1) for _ in range(6)]
        model = analysis.fit(trials)
        self.assertEqual(model["threshold_ms"], 80)
        self.assertEqual(analysis.decode(model, 10), 1)
        self.assertEqual(analysis.decode(model, 150), 0)
        reverse = [{"label": 1 - row["label"], "receiver_request": row["receiver_request"]} for row in trials]
        other = analysis.fit(reverse)
        self.assertEqual(analysis.decode(other, 150), 1)
        with self.assertRaises(ValueError):
            analysis.fit(trials[:-1])

    def test_holm_corrects_the_complete_four_test_family(self):
        self.assertEqual(analysis.holm([.001, .03, .02, .9]), [.004, .06, .06, .9])

    def test_direction_only_prediction_has_no_randomized_label_signal(self):
        trials = [{"receiver": actor, "label": label} for actor in ("alice", "bob")
                  for label in (0, 1) for _ in range(8)]
        predictions = [int(row["receiver"] == "alice") for row in trials]
        self.assertEqual(analysis.randomization_p(trials, predictions), 1)
        perfect = [row["label"] for row in trials]
        self.assertLess(analysis.randomization_p(trials, perfect), 1e-8)

    def test_existing_image_with_another_source_label_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SOURCES-FROZEN.json").write_text("{}")
            image = {"Id": "cache-image", "Config": {"Labels": {"org.research.source-freeze.sha256": "wrong"}}}
            with patch.object(service, "HERE", root), patch.object(service, "check_sources"), \
                    patch.object(service.old, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps([image]), "")), \
                    patch.object(service.old, "run_ok") as execute:
                with self.assertRaisesRegex(RuntimeError, "another source freeze"):
                    service.build_check()
                execute.assert_not_called()

    def test_incomplete_build_record_rejects_a_matching_binary_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SOURCES-FROZEN.json").write_text("{}")
            image = {"Id": "cache-image", "Config": {"Labels": {
                "org.research.source-freeze.sha256": service.sha(root / "SOURCES-FROZEN.json")}}}
            binary = {"nginx": {"version": "nginx/1.30.6", "exit_code": 0, "binary_sha256": "a"},
                      "squid": {"version": "Squid Cache: Version 7.8 --enable-http-violations",
                                "exit_code": 0, "binary_sha256": "b"}}
            folder = root / "build/g2"
            folder.mkdir(parents=True)
            (folder / "build-result.json").write_text('{"complete":false}')
            with patch.object(service, "HERE", root), patch.object(service, "check_sources"), \
                    patch.object(service.old, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps([image]), "")), \
                    patch.object(service.old, "run_ok", return_value=json.dumps(binary)):
                with self.assertRaisesRegex(RuntimeError, "complete matching build"):
                    service.build_check()


if __name__ == "__main__":
    unittest.main()
