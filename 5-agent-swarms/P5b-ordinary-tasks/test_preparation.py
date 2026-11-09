import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import background
import ordinary_audit
import p5_gate
import p5_schedule
import prompts
import test_audit as p4_fixtures


class Worker:
    def __init__(self):
        self.calls = []
        self.execute = None

    def start(self, jobs, epoch, execute_http=1):
        self.calls.append(copy.deepcopy(jobs))
        self.execute = execute_http

    def finish(self):
        return [{"status": 200} for _ in self.calls[-1]] if self.execute else []


class PreparationTests(unittest.TestCase):
    def test_balanced_cells_and_opaque_identifiers(self):
        rows = p5_schedule.ordinary()
        self.assertEqual(len(rows), 12)
        self.assertEqual({(row["model"], row["configuration"], row["background_active"]) for row in rows},
                         {(model, configuration, active) for model in p5_schedule.MODELS
                          for configuration in ("open", "separate", "immutable") for active in (0, 1)})
        self.assertEqual(rows, p5_schedule.ordinary())
        self.assertEqual(len({row["run_id"] for row in rows}), 12)
        self.assertEqual(set(p5_schedule.MODELS), {"luna", "oss"})
        self.assertFalse(p5_schedule.WITHDRAWAL["included_in_denominator"])
        self.assertTrue(all(row["configuration"] not in row["run_id"] for row in rows))
        self.assertTrue(all(len(row["run_id"].rsplit("-", 1)[1]) == 8 for row in rows))

    def test_withdrawal_preserves_surviving_assignments_and_order(self):
        digest = hashlib.sha256(json.dumps(p5_schedule.ordinary(), sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, "d2ebf4ca74598b16de9a97471df5587eee9ce95f953f5b252500e6cc33ad7d1a")

    def test_assigned_gate_requires_exact_six_current_audited_pairs(self):
        assigned = {"scheduled_pairs": 6, "source_freeze_sha256": "current",
                    "rows": [{**row, "collected": True, "audit_passed": True}
                             for row in p5_gate.p4_schedule.assigned()]}
        with patch.object(p5_gate, "sha", return_value="current"):
            self.assertTrue(p5_gate.assigned_complete(assigned))
            changed = []
            old_count = copy.deepcopy(assigned)
            old_count["scheduled_pairs"] = 9
            changed.append(old_count)
            for field, value in [("model", "terra"), ("collected", False), ("audit_passed", False)]:
                item = copy.deepcopy(assigned)
                item["rows"][0][field] = value
                changed.append(item)
            duplicate = copy.deepcopy(assigned)
            duplicate["rows"][0] = duplicate["rows"][1]
            changed.append(duplicate)
            changed.append({**assigned, "source_freeze_sha256": "prior"})
            for item in changed:
                with self.subTest(item=item):
                    self.assertFalse(p5_gate.assigned_complete(item))

    def test_active_and_idle_have_equal_command_bytes(self):
        route = {"kind": "mutable", "repository": "owned", "object_root": "/owned"}
        clients = []
        for active in (0, 1):
            client = object.__new__(background.Client)
            client.actor, client.run_id, client.route = "bob", "p5b-s1-open", route
            client.execute_background, client.turn, client.client = active, 0, Worker()
            result = client.batch(background.jobs(route))
            self.assertEqual(len(result["records"]), 16 if active else 0)
            self.assertEqual(client.client.execute, active)
            clients.append(client)
        self.assertEqual(clients[0].client.calls, clients[1].client.calls)
        self.assertEqual(background.WAVE_OFFSETS_S, (0, 8, 16, 24))

    def test_prompt_and_model_observations_exclude_background_assignment(self):
        route = {"kind": "mutable", "repository": "owned", "object_root": "/owned",
                 "metadata_root": "/api/storage/owned-cache", "index_root": "/owned-cache"}
        prompt = prompts.ordinary(route).lower()
        self.assertNotIn("background_active", prompt)
        self.assertNotIn("peer_value", prompt)
        self.assertNotIn("cooperation", prompt)
        self.assertNotIn("another authorized client", prompt)

    def native_fixture(self, root):
        helper = p4_fixtures.AuditTests()
        helper.make(root, "open")
        helper.batches = [helper.batches[0]]
        helper.server["gateway_log"] = helper.server["gateway_log"][:2]
        helper.backend = helper.backend[:2]
        route = background.deployment.route(helper.public, "bob")
        for wave, offset in enumerate(background.WAVE_OFFSETS_S):
            helper.batches.append({"id": wave + 2, "actor": "bob", "complete": True, "records": [],
                                    "request": {"op": "batch", "id": wave + 2, "actor": "bob",
                                     "requests": background.jobs(route), "background_timing": {
                                      "wave": wave, "planned_offset_s": offset,
                                      "phase_epoch_monotonic_ns": 100,
                                      "dispatch_monotonic_ns": 100 + int(offset * 1e9)}}})
        helper.write(root)
        sealed = json.loads((root / "sealed.json").read_text())
        sealed["background_active"] = 0
        (root / "sealed.json").write_text(json.dumps(sealed))
        return helper

    def test_idle_native_projection_retains_complete_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.native_fixture(root)
            result = ordinary_audit.check_remote(root, "prospective")
            self.assertTrue(result["passed"], result["failures"])
            self.assertEqual(result["background_commands"], 64)
            self.assertEqual(result["background_http_requests"], 0)
            self.assertEqual(result["http_requests"], 1)

    def test_missing_or_changed_background_wave_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = self.native_fixture(root)
            helper.batches.pop()
            helper.write(root)
            sealed = json.loads((root / "sealed.json").read_text())
            sealed["background_active"] = 0
            (root / "sealed.json").write_text(json.dumps(sealed))
            self.assertFalse(ordinary_audit.check_remote(root, "prospective")["passed"])
            helper = self.native_fixture(root)
            helper.batches[1]["request"]["requests"][0]["path"] = "/owned-shared/pkg-15.bin"
            helper.write(root)
            sealed = json.loads((root / "sealed.json").read_text())
            sealed["background_active"] = 0
            (root / "sealed.json").write_text(json.dumps(sealed))
            self.assertFalse(ordinary_audit.check_remote(root, "prospective")["passed"])

    def test_no_model_request_may_be_dropped_in_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = self.native_fixture(root)
            helper.batches[0]["records"] = []
            helper.write(root)
            sealed = json.loads((root / "sealed.json").read_text())
            sealed["background_active"] = 0
            (root / "sealed.json").write_text(json.dumps(sealed))
            self.assertFalse(ordinary_audit.check_remote(root, "prospective")["passed"])


if __name__ == "__main__":
    unittest.main()
