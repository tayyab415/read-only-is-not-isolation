import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import background
import history_audit
import ordinary_report
import p5_gate
import p5_schedule
import prompts
import provider
import test_audit as fixtures


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local, self.remote, self.fake_gate = (self.root / name for name in ("local", "remote", "gate"))
        for path in (self.local, self.remote, self.fake_gate):
            path.mkdir()
        (self.fake_gate / "SOURCES-FROZEN.json").write_text('{"synthetic":true}')
        self.source_sha = p5_gate.sha(self.fake_gate / "SOURCES-FROZEN.json")
        self.spec = next(row for row in p5_schedule.ordinary()
                         if row["configuration"] == "open" and row["background_active"] == 0)
        helper = fixtures.AuditTests()
        helper.make(self.remote, "open")
        old = helper.public["run_id"]
        helper.public = json.loads(json.dumps(helper.public).replace(old, self.spec["run_id"]))
        helper.server = json.loads(json.dumps(helper.server).replace(old, self.spec["run_id"]))
        helper.server["gateway_log"] = [{"ready": True, "edition": 2, "queue_size": 5}]
        helper.batches, helper.backend = [], []
        self.waves = []
        route = background.deployment.route(helper.public, "bob")
        for index, offset in enumerate(background.WAVE_OFFSETS_S):
            timing = {"wave": index, "planned_offset_s": offset, "phase_epoch_monotonic_ns": 100,
                      "dispatch_monotonic_ns": 100 + int(offset * 1e9)}
            returned = {"id": index + 1, "actor": "bob", "complete": True, "records": [],
                        "request": {"id": index + 1, "actor": "bob", "requests": background.jobs(route),
                                    "background_timing": timing}}
            helper.batches.append(returned)
            self.waves.append({**timing, "rpc_id": index + 1, "returned": returned})
        helper.write(self.remote)
        remote_seal = json.loads((self.remote / "sealed.json").read_text())
        remote_seal.update(source_freeze_sha256=self.source_sha, background_active=0)
        (self.remote / "sealed.json").write_text(json.dumps(remote_seal))
        self.public = helper.public
        route = background.deployment.route(self.public, "alice")
        config = json.loads((provider.BUDGET / "guard-config.json").read_text())
        self.request = {"modelId": config["model_ids"][self.spec["model"]],
                        "system": [{"text": provider.SYSTEM}],
                        "messages": [{"role": "user", "content": [{"text": prompts.ordinary(route)}]}],
                        "inferenceConfig": {"maxTokens": provider.MAX_TOKENS}}
        metadata = {"run_id": self.spec["run_id"], "actor": "alice", "turn": 0, "study": "ordinary"}
        self.budget = {"event": "committed", "call_key": "synthetic-key", "metadata": metadata,
                       "max_tokens": provider.MAX_TOKENS, "request_sha256": self.request_hash()}
        self.calls = [{"event": "reserved", "call_key": "synthetic-key", "metadata": metadata,
                       "start_utc_ns": 1, "request": self.request},
                      {"event": "completed", "call_key": "synthetic-key", "metadata": metadata,
                       "start_utc_ns": 1, "end_utc_ns": 2, "budget_event": self.budget,
                       "response": {"output": {"message": {"content": [
                           {"text": '{"action":"finish","status":"unknown"}'}]}}}}]
        self.decisions = [{"actor": "alice", "turn": 0, "call_key": "synthetic-key",
                           "returned_text": '{"action":"finish","status":"unknown"}', "dispatched": False,
                           "action": {"action": "finish", "status": "unknown", "note": "", "evidence": []}}]
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def request_hash(self):
        return hashlib.sha256(json.dumps(self.request, ensure_ascii=False).encode()).hexdigest()

    def write(self):
        (self.local / "preregister.json").write_text(json.dumps(self.spec))
        (self.local / "provision.json").write_text(json.dumps(self.public))
        (self.local / "calls.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.calls))
        (self.local / "client-actions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in self.decisions))
        (self.local / "background.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.waves))
        self.ledger = self.root / "ledger.jsonl"
        self.ledger.write_text(json.dumps(self.budget) + "\n")
        sealed = {"complete": True, "run_id": self.spec["run_id"], "source_freeze_sha256": self.source_sha,
                  "outcome": {"status": "finished"}, "remote_close": {
                      "status": json.loads((self.remote / "sealed.json").read_text())},
                  "file_sha256": {file.name: p5_gate.sha(file) for file in self.local.iterdir()
                                  if file.name != "sealed.json"}}
        (self.local / "sealed.json").write_text(json.dumps(sealed))

    def result(self):
        with patch.object(p5_gate, "HERE", self.fake_gate):
            return history_audit.check_cohort(self.local, self.remote, self.ledger)

    def test_exact_ordinary_history_and_matched_background(self):
        result = self.result()
        self.assertTrue(result["passed"], result["failures"])

    def test_hidden_assignment_in_prompt_fails_even_after_resealing(self):
        self.request["messages"][0]["content"][0]["text"] += "\nbackground_active=0"
        self.budget["request_sha256"] = self.request_hash()
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_changed_shadow_record_or_model_output_fails(self):
        self.waves[0]["returned"]["actor"] = "alice"
        self.write()
        self.assertFalse(self.result()["passed"])
        self.waves[0]["returned"]["actor"] = "bob"
        self.decisions[0]["returned_text"] = '{"action":"finish","status":"success"}'
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_identity_echo_does_not_become_discovery(self):
        result = ordinary_report.observations(
            {"users": {"bob": "observed-bob"}}, [{"turn": 1, "action": {"note": "A metadata value."},
             "records": [{"status": 200, "path": "/api/storage/owned-cache/pkg-01.bin?stats",
                          "body_text": '{"lastDownloadedBy":"observed-bob"}', "marker": "read",
                          "body_sha256": "hash"}]}])
        self.assertEqual(len(result["peer_linked_observations"]), 1)
        self.assertFalse(result["communication_claimed"])
        self.assertIsInstance(result["explicit_peer_attribution"], str)


if __name__ == "__main__":
    unittest.main()
