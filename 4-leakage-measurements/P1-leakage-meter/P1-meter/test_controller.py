"""Offline checks of the calibration/holdout retrieval boundary."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import controller


class ControllerGateTests(unittest.TestCase):
    def test_partial_calibration_never_retrieves_or_fits(self):
        snapshot = {run: {"calibration_trials": 300 if run == "open" else 0}
                    for run in controller.RUNS}
        with patch.object(controller, "policy_check"), patch.object(controller, "fetch") as fetch, \
                patch.object(controller, "analysis") as analysis:
            result = controller.advance(snapshot)
        self.assertEqual(result["stage"], "collecting_calibration")
        fetch.assert_not_called()
        analysis.assert_not_called()

    def test_holdout_retrieval_before_freeze_is_rejected(self):
        with patch.object(controller, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "requires the final freeze"):
                controller.fetch("open", "holdout.jsonl", Path("/unused"), frozen=False)
        command.assert_not_called()

    def test_private_credential_path_is_not_fetchable(self):
        with patch.object(controller, "command") as command:
            with self.assertRaisesRegex(ValueError, "allowlist"):
                controller.fetch("open", "credentials.json", Path("/unused"), frozen=True)
        command.assert_not_called()

    def test_changed_remote_file_is_not_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "open/calibration.jsonl"

            def fake_rsync(args, **_):
                Path(args[-1]).write_text("changed")
                return ""

            with patch.object(controller, "command", side_effect=fake_rsync):
                with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                    controller.fetch("open", "calibration.jsonl", Path(directory), "bad-hash")
            self.assertFalse(target.exists())

    def test_incomplete_seals_keep_holdout_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = root / "results/main"
            (root / "FROZEN.json").write_text(json.dumps(
                {"output_dir": "results/main", "sha256": {}, "utc": "frozen"}))
            snapshot = {run: {"calibration_trials": 300, "calibration_sha256": "cal",
                              "provision_sha256": "provision"} for run in controller.RUNS}
            with patch.object(controller, "HERE", root), patch.object(controller, "OUT", output), \
                    patch.object(controller, "policy_check"), patch.object(controller, "fetch") as fetch, \
                    patch.object(controller, "analysis") as analysis:
                result = controller.advance(snapshot)
            self.assertEqual(result["stage"], "models_frozen_waiting_for_seals")
            self.assertEqual(fetch.call_count, 10)
            self.assertTrue(all(call.args[1] != "holdout.jsonl" for call in fetch.call_args_list))
            analysis.assert_not_called()

    def test_changed_frozen_source_blocks_holdout_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "analysis.py").write_text("changed")
            (root / "FROZEN.json").write_text(json.dumps(
                {"output_dir": "results/main", "sha256": {"analysis.py": "expected"}, "utc": "frozen"}))
            snapshot = {run: {"calibration_trials": 300, "calibration_sha256": "cal",
                              "provision_sha256": "provision"} for run in controller.RUNS}
            with patch.object(controller, "HERE", root), patch.object(controller, "OUT", root / "results/main"), \
                    patch.object(controller, "policy_check"), patch.object(controller, "fetch") as fetch:
                with self.assertRaisesRegex(RuntimeError, "Post-freeze change"):
                    controller.advance(snapshot)
            self.assertTrue(all(call.args[1] != "holdout.jsonl" for call in fetch.call_args_list))


if __name__ == "__main__":
    unittest.main(verbosity=2)
