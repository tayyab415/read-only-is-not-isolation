"""Offline regression counterexamples for the two Astra findings.

These synthetic observations test analysis correctness. They are not lab data.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import analysis


def array_trial(index, bit):
    body = json.dumps({"request_id": f"{index:04d}", "values": [bit, 1 - bit]})
    return {"receiver": "bob", "sender": "alice", "pre": [], "post": [
        {"probe": "post.array", "status": 200, "elapsed_ms": 1.0, "headers": [],
         "body_text": body, "body_length": len(body),
         "body_sha256": hashlib.sha256(body.encode()).hexdigest()}]}


class AnalysisRegression(unittest.TestCase):
    def test_opposite_recipe_effects_remain_separate_on_holdout(self):
        y = np.array([0] * 150 + [1] * 150)
        values = np.array([0] * 150 + [-1] * 12 + [1] * 12 + [0] * 126, dtype=float)[:, None]
        recipes = np.array(["idle"] * 150 + ["minus"] * 12 + ["plus"] * 12 + ["other"] * 126)
        overall = analysis.feature_tests(values, y, ["response"], 1)
        by_recipe = {r: analysis.feature_tests(values, y, ["response"], 1, (y == 1) & (recipes == r))
                     for r in ["minus", "plus", "other"]}
        hypotheses = analysis.confirmation_hypotheses(overall, by_recipe)
        self.assertEqual(hypotheses, [{"feature": "response", "recipe": "minus"},
                                      {"feature": "response", "recipe": "plus"}])
        yh = np.array([0] * 500 + [1] * 500)
        xh = np.array([0] * 500 + [-1] * 42 + [1] * 42 + [0] * 416, dtype=float)[:, None]
        rh = np.array(["idle"] * 500 + ["minus"] * 42 + ["plus"] * 42 + ["other"] * 416)
        pooled = analysis.feature_tests(xh, yh, ["response"], 1)
        confirmed = analysis.confirm_hypotheses(xh, yh, rh, ["response"], 1, hypotheses)
        self.assertEqual(pooled[0]["p"], 1.0)
        self.assertTrue(all(r["p_holm_confirmatory"] < analysis.ALPHA for r in confirmed))
        self.assertEqual([r["n_pos"] for r in confirmed], [42, 42])
        self.assertEqual([r["effect"] for r in confirmed], [-1.0, 1.0])

    def test_unavailable_comparison_stays_in_complete_holm_family(self):
        x = np.array([[0.], [0.], [1.], [1.]])
        y = np.array([0, 0, 1, 1])
        recipes = np.array(["idle", "idle", "present", "present"])
        hypotheses = [{"feature": "response", "recipe": None},
                      {"feature": "response", "recipe": "present"},
                      {"feature": "response", "recipe": "absent"}]
        confirmed = analysis.confirm_hypotheses(x, y, recipes, ["response"], 1, hypotheses)
        self.assertEqual(len(confirmed), 3)
        self.assertEqual(confirmed[-1]["status"], "unavailable")
        self.assertEqual(confirmed[-1]["p"], 1.0)
        self.assertEqual([r["p_holm_confirmatory"] for r in confirmed],
                         analysis.holm([r["p"] for r in confirmed]))

    def test_constant_confirmation_is_retained(self):
        confirmed = analysis.confirm_hypotheses(np.zeros((4, 1)), np.array([0, 0, 1, 1]),
                                                np.array(["idle", "idle", "r", "r"]),
                                                ["response"], 1,
                                                [{"feature": "response", "recipe": "r"}])
        self.assertEqual(confirmed[0]["status"], "constant")
        self.assertEqual(confirmed[0]["p_holm_confirmatory"], 1.0)

    def test_arrays_preserve_scalar_values_positions_and_nested_fields(self):
        flat = {}
        analysis.flatten("json", {"values": [0, 1, None],
                                  "errors": [{"status": 404, "message": "missing"}],
                                  "children": [{"uri": "z"}, {"uri": "a"}]}, flat)
        self.assertEqual(flat["json.values[0]"], 0.0)
        self.assertEqual(flat["json.values[1]"], 1.0)
        self.assertEqual(flat["json.values[2]"], "null")
        self.assertEqual(flat["json.errors[0].status"], 404.0)
        self.assertEqual(flat["json.errors[0].message"], "missing")
        self.assertEqual(flat["json.children[0].uri"], "z")
        self.assertEqual(flat["json.children[1].uri"], "a")
        self.assertEqual(flat["json.children.uris"], "a|z")

    def test_fresh_ids_do_not_erase_array_bit_in_1000_unseen_trials(self):
        users = {"alice": "alice", "bob": "bob"}
        calibration = [analysis.trial_features(array_trial(i, i % 2), users) for i in range(300)]
        enc = analysis.Encoder().fit(calibration, [i % 2 for i in range(300)])
        unseen = [analysis.trial_features(array_trial(i + 300, i % 2), users) for i in range(1000)]
        rows = enc.transform(unseen)
        self.assertEqual(len(np.unique(rows, axis=0)), 2)
        value_col = enc.columns.index("post.array|json.values[0]")
        self.assertTrue(np.array_equal(rows[:, value_col], np.arange(1000) % 2))
        self.assertEqual(calibration[0]["post.array|body_sha"], unseen[0]["post.array|body_sha"])
        self.assertNotEqual(unseen[0]["post.array|body_sha"], unseen[1]["post.array|body_sha"])

    def test_receiver_request_identifiers_normalize_but_payload_stays(self):
        def record(index, message):
            filename = f"m1-open-t{index:04d}-a.bin"
            path = f"/repo/rdv/{filename}"
            body = json.dumps({"errors": [{"message": f"Path: 'repo:rdv/{filename}'; {message}"}]})
            return {"path": path, "marker": f"p1/m1-open/t{index:04d}/bob/post.rdv_a",
                    "status": 404, "elapsed_ms": 1., "body_text": body,
                    "body_length": len(body), "headers": []}
        a = analysis.probe_fields(record(1, "missing"), "bob", "alice")
        b = analysis.probe_fields(record(2, "missing"), "bob", "alice")
        c = analysis.probe_fields(record(3, "changed"), "bob", "alice")
        self.assertEqual(a["body_sha"], b["body_sha"])
        self.assertNotEqual(a["body_sha"], c["body_sha"])
        self.assertIn("missing", a["json.errors[0].message"])

    def test_clocks_and_request_headers_excluded_service_data_retained(self):
        body = json.dumps({"lastDownloaded": "2026-11-08T12:34:56Z", "values": [7, 8]})
        record = {"status": 200, "elapsed_ms": 1., "body_text": body, "body_length": len(body),
                  "headers": [("Date", "clock"), ("X-Request-Id", "request-0001"),
                              ("X-Artifactory-Id", "node-1"), ("X-JFrog-Version", "7.161.26")]}
        fields = analysis.probe_fields(record, "bob", "alice")
        self.assertNotIn("hdr.date", fields)
        self.assertNotIn("hdr.x-request-id", fields)
        self.assertEqual(fields["hdr.x-artifactory-id"], "node-1")
        self.assertEqual(fields["hdr.x-jfrog-version"], "7.161.26")
        self.assertEqual(fields["json.lastDownloaded"], "2026-11-08T12:34:56Z")
        self.assertEqual(fields["json.values[0]"], 7.0)

    def test_label_recipe_and_sender_records_cannot_change_receiver_features(self):
        trial = array_trial(1, 1)
        baseline = analysis.trial_features(trial, {"alice": "alice", "bob": "bob"})
        changed = copy.deepcopy(trial)
        changed.update(label=0, recipe="different", sender_records=[{"body_text": "controller secret"}])
        self.assertEqual(baseline, analysis.trial_features(changed, {"alice": "alice", "bob": "bob"}))

    def test_old_confirmation_schema_rejected(self):
        with self.assertRaises(ValueError):
            analysis.confirm_hypotheses(np.zeros((4, 1)), np.array([0, 0, 1, 1]),
                                       np.array(["idle", "idle", "r", "r"]), ["response"], 1,
                                       ["response"])

    def test_freeze_hashes_models_and_refuses_postfreeze_code_change(self):
        previous_here = analysis.HERE
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            analysis.HERE = root
            try:
                source = root / "analysis.py"
                source.write_bytes(Path(analysis.__file__).read_bytes())
                (root / "ANALYSIS-CODE-FROZEN.json").write_text(
                    json.dumps({"sha256": {"analysis.py": analysis.sha(source)}}))
                data, out = root / "data", root / "models"
                out.mkdir()
                manifest = {}
                for run in analysis.RUNS:
                    run_dir = data / run
                    run_dir.mkdir(parents=True)
                    (run_dir / "calibration.jsonl").write_text('{"label":0}\n' * 300)
                    (run_dir / "provision.json").write_text('{"users":{}}')
                    (out / f"{run}.model.pkl").write_bytes(b"unused regression placeholder")
                    manifest[run] = {"analysis_version": 2, "trials": 300,
                                     "calibration_sha256": analysis.sha(run_dir / "calibration.jsonl"),
                                     "provision_sha256": analysis.sha(run_dir / "provision.json")}
                (out / "calibration-results.json").write_text(json.dumps(manifest))
                result = analysis.freeze(data, out)
                self.assertEqual(result["freeze"]["models"], 5)
                frozen = json.loads((root / "FROZEN.json").read_text())
                self.assertTrue(all(f"models/{run}.model.pkl" in frozen["sha256"] for run in analysis.RUNS))
                source.write_text("changed after freezing")
                with self.assertRaisesRegex(SystemExit, "changed after the freeze"):
                    analysis.holdout(data, out)
            finally:
                analysis.HERE = previous_here


if __name__ == "__main__":
    unittest.main(verbosity=2)
