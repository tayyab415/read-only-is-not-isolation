"""Control-state and retrieval failures must leave holdout unopened."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import controller


def complete(phase):
    return {
        phase: {"stage": phase + "_audited"},
        phase + "_seals": {
            cfg: {"complete": True, "cleanup_absence_proved": True,
                  "trials": 300 if phase == "calibration" else 1000}
            for cfg in controller.CONFIGS},
    }


class ControllerTests(unittest.TestCase):
    def test_ready_requires_all_four_complete_cleaned_splits(self):
        value = complete("calibration")
        self.assertTrue(controller.ready(value, "calibration"))
        value["calibration_seals"]["aa"]["trials"] = 299
        self.assertFalse(controller.ready(value, "calibration"))
        value = complete("calibration")
        value["calibration_seals"]["immutable"]["cleanup_absence_proved"] = False
        self.assertFalse(controller.ready(value, "calibration"))

    def test_incomplete_holdout_does_not_pass_readiness(self):
        value = complete("holdout")
        value["holdout_seals"]["sham"]["complete"] = False
        self.assertFalse(controller.ready(value, "holdout"))

    def test_remote_failure_requires_lead_investigation(self):
        value = complete("calibration")
        value["calibration"]["stage"] = "failed"
        with self.assertRaises(RuntimeError):
            controller.ready(value, "calibration")

    def test_holdout_before_freeze_blocks_without_fetch(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(controller, "HERE", Path(temporary)), \
                patch.object(controller, "policy"), \
                patch.object(controller, "sha", return_value="same"), \
                patch.object(controller, "fetch_phase") as fetch:
            with self.assertRaises(RuntimeError):
                controller.advance({"source_sha256": "same", "holdout_files_exist": True,
                                    "sequence_sha256": {
                                        "calibration-sequence.py": "same",
                                        "holdout-sequence.py": "same"}})
            fetch.assert_not_called()

    def test_frozen_file_drift_blocks_before_holdout_fetch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fitted").write_text("changed")
            (root / "FROZEN.json").write_text(json.dumps({
                "data": "data/p2-m1", "out": "results/p2-m1",
                "holdout_collection_allowed": True,
                "sha256": {"fitted": hashlib.sha256(b"original").hexdigest()}}))
            with patch.object(controller, "HERE", root):
                with self.assertRaises(RuntimeError):
                    controller.verify_frozen_fit()

    def test_corrupt_transport_never_replaces_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            phase = "calibration"
            names = {phase + suffix for suffix in
                     [".jsonl", "-provision.json", "-schedule.json", "-server.json",
                      "-server-log-lines.txt", "-cleanup.json"]}
            expected = hashlib.sha256(b"correct").hexdigest()
            seal = {"sha256": expected, "provision_sha256": expected, "trials": 300,
                    "evidence_sha256": {n: expected for n in names
                                       if n not in {"calibration.jsonl",
                                                    "calibration-provision.json"}}}
            value = {"calibration_seals": {cfg: copy.deepcopy(seal)
                                           for cfg in controller.CONFIGS}}

            def corrupt(argv, **kwargs):
                Path(argv[-1]).write_text("corrupt")
                return ""

            with patch.object(controller, "DATA", root), \
                    patch.object(controller, "run", side_effect=corrupt):
                with self.assertRaises(RuntimeError):
                    controller.fetch_phase(value, "calibration")
            self.assertFalse((root / "separate/calibration-cleanup.json").exists())
            self.assertTrue((root / "separate/calibration-cleanup.json.retrieving").exists())
