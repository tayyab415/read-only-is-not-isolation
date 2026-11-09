"""Adversarial changes must fail the P1 join audit."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import audit_v2 as audit

HERE = Path(__file__).resolve().parent.parent / "P1-meter"


class JoinAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name) / "open"
        shutil.copytree(HERE / "data/smoke-s1/open", self.folder)
        self.server_path = self.folder / "server.json"
        self.server = json.loads(self.server_path.read_text())

    def tearDown(self):
        self.temporary.cleanup()

    def evaluate(self):
        self.server_path.write_text(json.dumps(self.server))
        return audit.audit_run(self.folder, "open", True)

    def mutate_router(self, mutate):
        path = self.folder / "server-log-lines.txt"
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("{"))
        row = json.loads(lines[index])
        mutate(row)
        lines[index] = json.dumps(row)
        path.write_text("\n".join(lines) + "\n")

    def test_unchanged_native_smoke_passes(self):
        self.assertTrue(self.evaluate()["passed"])

    def test_wrong_trace_fails(self):
        self.mutate_router(lambda row: row.update(TraceId="unrelated-trace"))
        self.assertFalse(self.evaluate()["passed"])

    def test_completion_outside_client_interval_fails(self):
        self.mutate_router(lambda row: row.update(Duration=10_000_000_000))
        self.assertFalse(self.evaluate()["passed"])

    def test_negative_duration_fails(self):
        self.mutate_router(lambda row: row.update(Duration=-1))
        self.assertFalse(self.evaluate()["passed"])

    def test_missing_gateway_record_fails(self):
        self.server["gateway_log"].pop(0)
        result = self.evaluate()
        self.assertFalse(result["passed"])
        self.assertTrue(result["invalid_trials"])

    def test_duplicate_gateway_record_fails(self):
        self.server["gateway_log"].append(self.server["gateway_log"][0])
        self.assertFalse(self.evaluate()["passed"])

    def test_principal_mismatch_fails(self):
        self.server["gateway_log"][0]["principal"] = "unrelated-principal"
        self.assertFalse(self.evaluate()["passed"])

    def test_router_query_loss_fails(self):
        log_path = self.folder / "server-log-lines.txt"
        records = log_path.read_text().splitlines()
        changed = False
        for index, line in enumerate(records):
            if not line.startswith("{"):
                continue
            row = json.loads(line)
            if "?" in row["RequestPath"]:
                row["RequestPath"] = row["RequestPath"].partition("?")[0]
                records[index] = json.dumps(row)
                changed = True
                break
        self.assertTrue(changed)
        log_path.write_text("\n".join(records) + "\n")
        self.assertFalse(self.evaluate()["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
