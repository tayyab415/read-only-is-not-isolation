import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import validate


def fixture():
    trials, upstream = [], []
    for index in range(20):
        label = index % 2
        start = 10_000_000_000 + index * 1_000_000_000
        path = f"/owned/rdv/case-{index}.bin"
        before = {"probe": "pre.stats.03", "status": 200,
                  "body_text": json.dumps({"downloadCount": index, "lastDownloadedBy": "bob"}),
                  "end_utc_ns": start, "marker": f"before-{index}"}
        after = {"probe": "post.stats.03", "status": 200,
                 "body_text": json.dumps({"downloadCount": index + label,
                                         "lastDownloadedBy": "alice" if label else "bob"}),
                 "start_utc_ns": start + 300_000_000, "marker": f"after-{index}"}
        sender = {"method": "GET", "actor": "alice", "status": 200, "path": "/owned/pkg-03.bin",
                  "start_utc_ns": start + 10_000_000, "end_utc_ns": start + 190_000_000,
                  "marker": f"send-{index}"}
        receiver = {"probe": "post.rdv_a", "method": "GET", "status": 404, "path": path,
                    "start_utc_ns": start + 300_000_000, "end_utc_ns": start + 500_000_000,
                    "elapsed_ms": 200, "body_text": json.dumps({"errors": [{"status": 404,
                     "message": f"Not Found; Path: 'owned:rdv/case-{index}.bin'"}]}),
                    "headers": [], "body_length": 80, "body_sha256": "not-used",
                    "marker": f"recv-{index}"}
        trial = {"label": label, "recipe": "get", "trial_id": f"t{index}", "value": 3,
                 "sender": "alice", "receiver": "bob", "sender_records": [sender] if label else [],
                 "pre": [before], "post": [after, receiver]}
        trials.append(trial)
        owner = sender if label else receiver
        upstream.append({"path": f"/rdv/case-{index}.bin", "method": "GET", "status": 404,
                         "delay_ms": 150, "body_bytes": 19,
                         "start_utc_ns": owner["start_utc_ns"] + 1_000_000,
                         "end_utc_ns": owner["start_utc_ns"] + 151_000_000})
    return trials, upstream


class ValidationTests(unittest.TestCase):
    def test_statistics_all_eligible_trials(self):
        trials, _ = fixture()
        result = validate.statistics(trials, {"alice": "alice", "bob": "bob"})
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["evidence"]), 20)
        trials[3]["post"][0]["body_text"] = json.dumps(
            {"downloadCount": 3, "lastDownloadedBy": "alice"})
        result = validate.statistics(trials, {"alice": "alice", "bob": "bob"})
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["trial"], "t3")

    def test_wrong_principal_cannot_explain_stats(self):
        trials, _ = fixture()
        trials[1]["post"][0]["body_text"] = json.dumps(
            {"downloadCount": 2, "lastDownloadedBy": "bob"})
        self.assertFalse(validate.statistics(trials, {"alice": "alice", "bob": "bob"})["passed"])

    def test_negative_cache_counts_and_intervals(self):
        trials, upstream = fixture()
        for trial in trials:
            trial["recipe"] = "rdv_get"
            if trial["sender_records"]:
                trial["sender_records"][0].update(path=trial["post"][1]["path"], status=404)
        result = validate.negative_cache(trials, upstream, {"alice": "alice", "bob": "bob"},
                                         validate.core())
        self.assertTrue(result["passed"], result)
        self.assertEqual(len(result["evidence"]), 20)
        duplicate = copy.deepcopy(upstream[1])
        self.assertFalse(validate.negative_cache(trials, upstream + [duplicate],
                         {"alice": "alice", "bob": "bob"}, validate.core())["passed"])
        wrong = copy.deepcopy(upstream)
        wrong[1].update(start_utc_ns=trials[1]["post"][1]["start_utc_ns"] + 1_000_000,
                        end_utc_ns=trials[1]["post"][1]["start_utc_ns"] + 151_000_000)
        self.assertFalse(validate.negative_cache(trials, wrong, {"alice": "alice", "bob": "bob"},
                                                validate.core())["passed"])

    def test_response_content_cannot_carry_different_labels(self):
        trials, upstream = fixture()
        for trial in trials:
            trial["recipe"] = "rdv_get"
            if trial["sender_records"]:
                trial["sender_records"][0].update(path=trial["post"][1]["path"], status=404)
                trial["post"][1]["body_text"] = '{"errors":[{"status":404,"message":"different"}]}'
        self.assertFalse(validate.negative_cache(trials, upstream, {"alice": "alice", "bob": "bob"},
                                                validate.core())["passed"])

    def test_frozen_selection_and_complete_correction_required(self):
        row = {"run": "open", "recipe": "rdv_get", "feature": "post.rdv_a|elapsed_ms",
               "p_holm_experiment": 0.001}
        calibration = {"open": {"selected_for_confirmation": [
            {"feature": row["feature"], "recipe": row["recipe"]}]}}
        self.assertEqual(len(validate.selected_confirmations(
            {"confirmations": [row]}, calibration, "negative_cache")), 1)
        self.assertFalse(validate.selected_confirmations(
            {"confirmations": [{**row, "p_holm_experiment": 0.1}]}, calibration, "negative_cache"))
        self.assertFalse(validate.selected_confirmations(
            {"confirmations": [row]}, {"open": {"selected_for_confirmation": []}}, "negative_cache"))

    def test_no_main_freeze_blocks_data_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VALIDATION-FROZEN.json").write_text(json.dumps(
                {"sha256": {}, "p1_import_sha256": {}}))
            with patch.object(validate, "HERE", root), patch.object(validate, "P1", root):
                with self.assertRaises(FileNotFoundError):
                    validate.validate(root / "nonexistent-data", root / "nonexistent-results")

    def test_small_cell_never_passes(self):
        trials, _ = fixture()
        result = validate.statistics(trials[:2], {"alice": "alice", "bob": "bob"})
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
