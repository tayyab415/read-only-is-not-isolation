"""Check retained failures, sealing, and cleanup without VM contact."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import collect


class FakeClient:
    def close(self):
        pass


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "smoke-p2-s2"
        self.state = self.root / "state.json"
        self.state.write_text("{}")
        self.cleanup = {"absence_proved": True, "containers_readback": [],
                        "networks_readback": [], "art_a_p1_networks": []}
        self.patches = [
            patch.object(collect.service, "check_gate", return_value={}),
            patch.object(collect.service, "provision", return_value={
                "run_id": "p2-s2-c-separate", "users": {"alice": "a", "bob": "b"}}),
            patch.object(collect.service.old, "load_state", return_value={}),
            patch.object(collect.service.old, "state_path", return_value=self.state),
            patch.object(collect.harness.Client, "sandbox", return_value=FakeClient()),
            patch.object(collect.harness, "run_trial", side_effect=lambda state, run, cfg, row, *args:
                         {**row, "batch_must_stop": False, "post": [], "sender_records": []}),
            patch.object(collect.service, "collect", return_value=({"run_id": "p2-s2-c-separate"}, [])),
            patch.object(collect.service, "cleanup", return_value=self.cleanup),
            patch.object(collect.time, "sleep", return_value=None),
            patch("collect.print", create=True),
        ]
        self.mocks = [patcher.start() for patcher in self.patches]

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def test_missing_gate_stops_before_provision(self):
        self.mocks[0].side_effect = RuntimeError("Not validated")
        with self.assertRaises(RuntimeError):
            collect.execute(self.data, "separate", "calibration", smoke=True)
        self.mocks[1].assert_not_called()
        self.assertFalse(self.data.exists())

    def test_success_seals_all_evidence_after_cleanup(self):
        result = collect.execute(self.data, "separate", "calibration", smoke=True)
        self.assertTrue(result["complete"])
        self.assertEqual(result["trials"], 8)
        self.assertEqual(len(result["evidence_sha256"]), 4)
        self.mocks[7].assert_called_once_with("p2-s2-c-separate")
        for name, expected in result["evidence_sha256"].items():
            self.assertEqual(collect.service.sha(self.data / "separate" / name), expected)

    def test_failed_trial_is_retained_and_cleanup_runs(self):
        self.mocks[5].side_effect = lambda state, run, cfg, row, *args: {
            **row, "batch_must_stop": True, "post": [], "sender_records": []}
        with self.assertRaises(RuntimeError):
            collect.execute(self.data, "separate", "calibration", smoke=True)
        folder = self.data / "separate"
        self.assertEqual(len((folder / "calibration.jsonl").read_text().splitlines()), 1)
        sealed = json.loads((folder / "calibration-sealed.json").read_text())
        self.assertFalse(sealed["complete"])
        self.assertEqual(sealed["retained_trials"], 1)
        self.mocks[7].assert_called_once()

    def test_evidence_failure_cannot_be_sealed_as_complete(self):
        self.mocks[6].side_effect = OSError("Unavailable evidence")
        with self.assertRaises(RuntimeError):
            collect.execute(self.data, "separate", "calibration", smoke=True)
        folder = self.data / "separate"
        self.assertEqual(len((folder / "calibration.jsonl").read_text().splitlines()), 8)
        sealed = json.loads((folder / "calibration-sealed.json").read_text())
        self.assertFalse(sealed["complete"])
        self.assertEqual(sealed["evidence_failure_type"], "OSError")
        self.mocks[7].assert_called_once()

    def test_cleanup_failure_blocks_a_complete_verdict(self):
        self.cleanup["absence_proved"] = False
        with self.assertRaises(RuntimeError):
            collect.execute(self.data, "separate", "calibration", smoke=True)
        sealed = json.loads((self.data / "separate/calibration-sealed.json").read_text())
        self.assertFalse(sealed["cleanup_absence_proved"])
        self.assertNotIn("sha256", sealed)

    def test_holdout_cannot_start_before_model_freeze(self):
        with patch.object(collect, "HERE", self.root):
            with self.assertRaises(FileNotFoundError):
                collect.execute(self.root / "p2-m1", "separate", "holdout")
        self.mocks[1].assert_not_called()

    def test_worker_stop_failure_does_not_skip_service_cleanup(self):
        with patch.object(FakeClient, "close", side_effect=OSError("Private worker failure")):
            with self.assertRaises(RuntimeError):
                collect.execute(self.data, "separate", "calibration", smoke=True)
        self.mocks[7].assert_called_once()
        sealed = json.loads((self.data / "separate/calibration-sealed.json").read_text())
        self.assertFalse(sealed["complete"])
        self.assertEqual(sealed["worker_stop_failure_type"], "OSError")


if __name__ == "__main__":
    unittest.main()
