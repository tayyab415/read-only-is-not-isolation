import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import service


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        evidence = self.root / "gate-evidence"
        evidence.mkdir()
        (self.root / "SOURCES-FROZEN.json").write_text("{}")
        inputs = {"holdout-results.json": {}, "FROZEN.json": {}, "REPORTING-FROZEN.json": {},
                  "known-channel-validation.json": {"mechanisms": {
                      name: {"passed": True, "evidence": ["fixed-counterexample"]}
                      for name in ["download_statistics", "negative_cache"]}},
                  "audit-results.json": {"passed": True, "smoke": False, "runs": [
                      {"run": name, "trials": 1300, "passed": True}
                      for name in ["open", "combined", "separate", "aa", "sham"]]}}
        for name, value in inputs.items():
            (evidence / name).write_text(json.dumps(value))
        metrics = {"null_gate_passed": True,
                   "input_sha256": service.sha(evidence / "holdout-results.json"),
                   "reporting_freeze_sha256": service.sha(evidence / "REPORTING-FROZEN.json")}
        (evidence / "report-metrics.json").write_text(json.dumps(metrics))
        self.allow = {name: True for name in ["p1_validated", "known_statistics_validated",
                                             "known_negative_cache_validated", "p1_causal_audit_passed",
                                             "p1_nulls_passed"]}
        self.allow.update(p2_source_freeze_sha256=service.sha(self.root / "SOURCES-FROZEN.json"),
                          evidence_sha256={path.name: service.sha(path) for path in evidence.iterdir()})
        self.write_allow()
        self.patches = [patch.object(service, "HERE", self.root),
                        patch.object(service, "check_sources", return_value={}),
                        patch.object(service.old, "run_ok", return_value="unrelated-existing-service\n"),
                        patch.object(service.old, "free_gib", return_value=8)]
        self.mocks = [patcher.start() for patcher in self.patches]

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def write_allow(self):
        (self.root / "ALLOW-COLLECTION.json").write_text(json.dumps(self.allow))

    def change_evidence(self, name, mutate):
        path = self.root / "gate-evidence" / name
        row = json.loads(path.read_text())
        mutate(row)
        path.write_text(json.dumps(row))
        self.allow["evidence_sha256"][name] = service.sha(path)
        self.write_allow()

    def test_complete_gate_passes_without_any_mutation(self):
        self.assertTrue(service.check_gate()["p1_validated"])
        self.mocks[2].assert_called_once_with(["docker", "ps", "--format", "{{.Names}}"])

    def test_pending_or_failed_p1_decision_blocks(self):
        self.allow["p1_validated"] = False
        self.write_allow()
        with self.assertRaises(RuntimeError):
            service.check_gate()
        self.mocks[2].assert_not_called()

    def test_missing_evidence_hash_blocks(self):
        del self.allow["evidence_sha256"]["known-channel-validation.json"]
        self.write_allow()
        with self.assertRaises(RuntimeError):
            service.check_gate()

    def test_smoke_audit_cannot_authorize_main_collection(self):
        self.change_evidence("audit-results.json", lambda row: row.update(smoke=True))
        with self.assertRaises(RuntimeError):
            service.check_gate()

    def test_incomplete_audit_and_failed_null_block(self):
        self.change_evidence("audit-results.json", lambda row: row["runs"][0].update(trials=20))
        with self.assertRaises(RuntimeError):
            service.check_gate()
        self.change_evidence("audit-results.json", lambda row: row["runs"][0].update(trials=1300))
        self.change_evidence("report-metrics.json", lambda row: row.update(null_gate_passed=False))
        with self.assertRaises(RuntimeError):
            service.check_gate()

    def test_wrong_reporting_input_hash_blocks(self):
        self.change_evidence("report-metrics.json", lambda row: row.update(input_sha256="wrong"))
        with self.assertRaises(RuntimeError):
            service.check_gate()

    def test_active_p1_or_low_memory_blocks(self):
        self.mocks[2].return_value = "p1-m1-sham-alice\n"
        with self.assertRaises(RuntimeError):
            service.check_gate()
        self.mocks[2].return_value = ""
        self.mocks[3].return_value = 1.5
        with self.assertRaises(RuntimeError):
            service.check_gate()

    def test_changed_gate_evidence_is_rejected(self):
        (self.root / "gate-evidence/audit-results.json").write_text("{}")
        with self.assertRaises(RuntimeError):
            service.check_gate()


if __name__ == "__main__":
    unittest.main()
