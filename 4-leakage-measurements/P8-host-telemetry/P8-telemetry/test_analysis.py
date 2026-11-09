import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analysis
import audit
import collector
import experiment as e
from test_audit import fixture, write_fixture


class AnalysisTests(unittest.TestCase):
    def setup_inputs(self, root):
        data = root / "data/p8-m1"
        for configuration in e.CONFIGURATIONS:
            write_fixture(data, configuration, fixture(configuration, "calibration"), split="calibration")
        (root / "SOURCES-FROZEN.json").write_bytes((data / "SOURCES-FROZEN.json").read_bytes())
        (root / "audit.py").write_bytes(Path(audit.__file__).read_bytes())
        with patch.object(audit, "HERE", root):
            result = audit.audit_all(data, "calibration")
        self.assertTrue(result["passed"], result)
        (data / "calibration-audit-results.json").write_text(json.dumps(result))
        return data

    def test_all_four_calibrations_freeze_before_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.setup_inputs(root)
            with patch.object(analysis, "HERE", root), patch.object(collector, "HERE", root), \
                    patch.object(analysis.runtime, "check_sources", return_value={}):
                result = analysis.calibrate(data, root / "results")
                self.assertEqual(set(result["models"]), set(e.CONFIGURATIONS))
                frozen = collector.verify_freeze(data)
                self.assertTrue(frozen["all_models_frozen_before_holdout"])
                self.assertEqual(frozen["data"], "data/p8-m1")
                self.assertEqual(len(frozen["sha256"]), 31)

    def test_changed_calibration_input_breaks_holdout_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.setup_inputs(root)
            with patch.object(analysis, "HERE", root), patch.object(collector, "HERE", root), \
                    patch.object(analysis.runtime, "check_sources", return_value={}):
                analysis.calibrate(data, root / "results")
                path = data / "native/calibration.jsonl"
                path.write_text(path.read_text() + "\n")
                with self.assertRaises(RuntimeError):
                    collector.verify_freeze(data)

    def test_unfrozen_or_partial_calibrations_cannot_proceed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.setup_inputs(root)
            contents = json.loads((data / "calibration-audit-results.json").read_text())
            contents["runs"].pop()
            (data / "calibration-audit-results.json").write_text(json.dumps(contents))
            with patch.object(analysis, "HERE", root), \
                    patch.object(analysis.runtime, "check_sources", return_value={}):
                with self.assertRaises(RuntimeError):
                    analysis.calibrate(data, root / "results")
                self.assertFalse((root / "FROZEN.json").exists())

    def test_previous_holdout_blocks_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.setup_inputs(root)
            (data / "aa-masked/holdout.jsonl").write_text("retained\n")
            with patch.object(analysis, "HERE", root), \
                    patch.object(analysis.runtime, "check_sources", return_value={}):
                with self.assertRaises(RuntimeError):
                    analysis.calibrate(data, root / "results")

    def test_iid_interval_labels_and_extremes(self):
        self.assertEqual(analysis.interval(0, 64)[0], 0)
        self.assertEqual(analysis.interval(64, 64)[1], 1)
        self.assertGreater(analysis.interval(32, 64)[1], .5)
        self.assertLess(analysis.interval(32, 64)[0], .5)

    def test_missing_native_smoke_blocks_main_before_provisioning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collector, "HERE", root), patch.object(analysis.runtime, "HERE", root), \
                    patch.object(analysis.runtime, "check_gate", return_value={}), \
                    patch.object(analysis.runtime, "provision") as provision:
                with self.assertRaises(FileNotFoundError):
                    collector.execute("native", "calibration")
                provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
