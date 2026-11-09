import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import call_audit
import gate
import prompts
import provider
import schedule
import test_audit as fixtures


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local, self.remote = self.root / "local", self.root / "remote"
        self.local.mkdir()
        self.remote.mkdir()
        self.fake_gate = self.root / "gate"
        self.fake_gate.mkdir()
        (self.fake_gate / "SOURCES-FROZEN.json").write_text('{"synthetic":true}')
        self.ledger_path = self.root / "ledger.jsonl"
        helper = fixtures.AuditTests()
        helper.make(self.remote, "open")
        spec = next(row for row in schedule.assigned() if row["configuration"] == "open")
        self.spec = spec
        helper.public["run_id"] = spec["run_id"]
        helper.server["run_id"] = spec["run_id"]
        boundary = helper.public["boundary_readback"]
        network_map = {}
        for role, old_name in helper.public["networks"].items():
            network_map[old_name] = f"p1-{spec['run_id']}-net-{role}"
        for role in helper.public["networks"]:
            helper.public["networks"][role] = network_map[helper.public["networks"][role]]
            boundary["networks"][role]["name"] = helper.public["networks"][role]
        for role in helper.public["containers"]:
            helper.public["containers"][role] = f"p1-{spec['run_id']}-{role}"
            row = boundary["containers"][role]
            row["name"] = helper.public["containers"][role]
            row["attached_networks"] = [network_map[name] for name in row["attached_networks"]]
            row["network_addresses"] = {network_map[name]: address
                                         for name, address in row["network_addresses"].items()}
        helper.batches, helper.backend = [], []
        helper.server["gateway_log"] = [{"ready": True, "edition": 2, "queue_size": 5}]
        helper.write(self.remote)
        remote_seal = json.loads((self.remote / "sealed.json").read_text())
        remote_seal["source_freeze_sha256"] = gate.sha(self.fake_gate / "SOURCES-FROZEN.json")
        (self.remote / "sealed.json").write_text(json.dumps(remote_seal))
        self.public = helper.public
        self.calls, self.ledger, self.decisions = [], [], []
        self.outcomes = {}
        config = json.loads((provider.BUDGET / "guard-config.json").read_text())
        for actor in ("alice", "bob"):
            peer = "bob" if actor == "alice" else "alice"
            route = {"kind": "mutable", "repository": "owned-shared", "object_root": "/owned-shared",
                     "metadata_root": "/api/storage/owned-shared-cache", "index_root": "/owned-shared-cache"}
            prompt = prompts.assigned(actor, peer, spec["values"][actor], route,
                                      helper.public["users"][peer], spec["public_token"])
            request = {"modelId": config["model_ids"][spec["model"]],
                       "system": [{"text": provider.SYSTEM}],
                       "messages": [{"role": "user", "content": [{"text": prompt}]}],
                       "inferenceConfig": {"maxTokens": provider.MAX_TOKENS}}
            text = '{"action":"finish","status":"unknown"}'
            metadata = {"run_id": spec["run_id"], "actor": actor, "turn": 0, "study": "assigned"}
            key = f"synthetic-{actor}"
            budget = {"event": "committed", "call_key": key, "metadata": metadata,
                      "request_sha256": hashlib.sha256(
                          json.dumps(request, ensure_ascii=False).encode()).hexdigest(),
                      "max_tokens": provider.MAX_TOKENS, "usd": "0.001"}
            self.ledger.append(budget)
            self.calls.extend([{"event": "reserved", "call_key": key, "metadata": metadata,
                                "start_utc_ns": 1, "request": request},
                               {"event": "completed", "call_key": key, "metadata": metadata,
                                "start_utc_ns": 1, "end_utc_ns": 2,
                                "response": {"output": {"message": {"content": [{"text": text}]}},
                                             "usage": {"inputTokens": 50, "outputTokens": 10}},
                                "budget_event": budget}])
            self.decisions.append({"actor": actor, "turn": 0, "call_key": key,
                                   "returned_text": text, "dispatched": False,
                                   "action": {"action": "finish", "status": "unknown", "note": "", "evidence": []}})
            self.outcomes[actor] = {"status": "finished"}
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        (self.local / "preregister.json").write_text(json.dumps(self.spec))
        (self.local / "provision.json").write_text(json.dumps(self.public))
        (self.local / "calls.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.calls))
        (self.local / "client-actions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in self.decisions))
        self.ledger_path.write_text("".join(json.dumps(row) + "\n" for row in self.ledger))
        sealed = {"complete": True, "run_id": self.spec["run_id"], "outcomes": self.outcomes,
                  "source_freeze_sha256": gate.sha(self.fake_gate / "SOURCES-FROZEN.json"),
                  "remote_close": {"status": json.loads((self.remote / "sealed.json").read_text())},
                  "file_sha256": {file.name: gate.sha(file) for file in self.local.iterdir()
                                  if file.name != "sealed.json"}}
        (self.local / "sealed.json").write_text(json.dumps(sealed))

    def result(self):
        with patch.object(gate, "HERE", self.fake_gate):
            return call_audit.check_cohort(self.local, self.remote, self.ledger_path)

    def test_private_histories_and_ledger_match(self):
        result = self.result()
        self.assertTrue(result["passed"], result["failures"])

    def test_injected_peer_information_fails_after_resealing(self):
        self.calls[0]["request"]["messages"][0]["content"][0]["text"] += "\nPeer secret is 7."
        self.ledger[0]["request_sha256"] = hashlib.sha256(
            json.dumps(self.calls[0]["request"], ensure_ascii=False).encode()).hexdigest()
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_changed_output_and_missing_reservation_fail(self):
        self.decisions[0]["returned_text"] = '{"action":"finish","status":"success"}'
        self.write()
        self.assertFalse(self.result()["passed"])
        self.decisions[0]["returned_text"] = '{"action":"finish","status":"unknown"}'
        self.ledger.pop()
        self.write()
        self.assertFalse(self.result()["passed"])


if __name__ == "__main__":
    unittest.main()
