#!/usr/bin/env python3
"""No-model regressions. Run directly; never creates a Bedrock client."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import run
import scripted
import controller_guard
from controller_guard import controller_lock


class FakeClient:
    def __init__(self, mode):
        self.mode = mode
        self.requests = []

    def converse(self, **request):
        # Copy immediately because the real Bedrock call serializes history.
        self.requests.append(json.loads(json.dumps(request)))
        if self.mode == "fail":
            raise RuntimeError("test failure")
        is_final = any(c.get("text") == run.FINAL_ACTION for c in request["messages"][-1]["content"])
        action = {"action": "finish", "status": "unknown", "peer_value": None, "xor": None} if is_final and self.mode != "ignore_final" else {"action": "wait", "seconds": 1}
        result = {"output": {"message": {"role": "assistant", "content": [{"text": json.dumps(action)}]}}}
        if self.mode != "missing_usage":
            result["usage"] = {"inputTokens": 5, "outputTokens": 5}
        return result


class HarnessTests(unittest.TestCase):
    def test_six_concurrent_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = run.Ledger(Path(tmp) / "ledger.jsonl")
            barrier = threading.Barrier(6)
            def reserve(i):
                barrier.wait()
                return ledger.reserve({"call_key": str(i), "usd": .10}, .26)
            with ThreadPoolExecutor(max_workers=6) as pool:
                accepted = list(pool.map(reserve, range(6)))
            self.assertEqual(sum(accepted), 2)
            self.assertAlmostEqual(ledger.current_total(), .20)
            self.assertLessEqual(ledger.current_total(), .26)
            self.assertTrue(ledger.reserve({"call_key": "exact", "usd": .06}, .26))
            self.assertFalse(ledger.reserve({"call_key": "over", "usd": .000001}, .26))
            print("six-thread cap test: accepted 2, refused 4; total $0.20 <= $0.26; exact $0.26 accepted")

    def test_reservation_formula(self):
        request = {"text": "some text"}
        expected = 1.2 * ((len(json.dumps(request).encode()) / 2) * .1375 + 1536 * .55) / 1e6
        self.assertEqual(run.reservation_usd("luna", request, 1536), expected)

    def test_missing_usage_and_failure_keep_reservation(self):
        for mode in ("missing_usage", "fail"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                ledger = run.Ledger(d / "ledger.jsonl")
                with patch.object(run, "HARD_CAP", 1):
                    run.infer(FakeClient(mode), "luna", "test", "alice", 0, [], ledger, d, run.CohortClock())
                rows = [json.loads(l) for l in ledger.path.read_text().splitlines()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["event"], "reserved")
                self.assertGreater(ledger.current_total(), 0)
                self.assertTrue(rows[0]["call_key"].startswith(run.RUN_ID + "/"))

    def test_final_prompt_and_limit(self):
        for mode, status in (("normal", "unknown"), ("ignore_final", "action_limit")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                client = FakeClient(mode)
                with patch.object(run.time, "sleep"), patch.object(run, "HARD_CAP", 1):
                    result = run.agent_loop("test", "alice", "bob", "luna", "prompt", "n-test-pkg", 3,
                                            run.CohortClock(), run.Ledger(d / "ledger.jsonl"), client, d)
                self.assertEqual(result["status"], status)
                self.assertEqual(len(client.requests), 3)
                self.assertIn(run.FINAL_ACTION, [c.get("text") for c in client.requests[-1]["messages"][-1]["content"]])
                actions = [json.loads(l) for l in (d / "actions.jsonl").read_text().splitlines()]
                self.assertEqual(len(actions), 3)
                if mode == "ignore_final":
                    self.assertIs(actions[-1]["dispatched"], False)

    def test_deadline_before_dispatch(self):
        class SlowClient(FakeClient):
            def converse(self, **request):
                answer = super().converse(**request)
                clock.start -= run.WALL_LIMIT_S + 1
                return answer
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            clock = run.CohortClock()
            with patch.object(run, "service") as service:
                outcome = run.agent_loop("test", "alice", "bob", "luna", "prompt", "n-test-pkg", 3,
                                         clock, run.Ledger(d / "ledger.jsonl"), SlowClient("normal"), d)
            self.assertEqual(outcome["status"], "time_limit")
            service.assert_not_called()

    def test_service_failure_is_infra_error(self):
        class HttpClient(FakeClient):
            def converse(self, **request):
                return {"usage": {"inputTokens": 5, "outputTokens": 5},
                        "output": {"message": {"content": [{"text": json.dumps({
                            "action": "http", "method": "GET", "path": "/n-test-pkg/pkg-00.bin"})}]}}}
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with patch.object(run, "service", side_effect=RuntimeError("lab host failure")):
                outcome = run.agent_loop("test", "alice", "bob", "luna", "prompt", "n-test-pkg", 3,
                                         run.CohortClock(), run.Ledger(d / "ledger.jsonl"), HttpClient("normal"), d)
            self.assertEqual(outcome["status"], "infra_error")

    def test_controller_lock(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(controller_guard, "ROOT", Path(tmp)), controller_lock():
            proc = subprocess.run(["python3", "-c", "import controller_guard, sys\nfrom pathlib import Path\ncontroller_guard.ROOT=Path(sys.argv[1])\nwith controller_guard.controller_lock(): pass", tmp],
                                  cwd=Path(__file__).parent, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("another N controller", proc.stderr)

    def test_plans_and_duplicate_ids(self):
        for stage, count in (("A3", 6), ("A2", 6), ("A1", 6), ("A0", 6), ("B3", 30), ("B2", 30), ("BN", 6)):
            plan = run.make_plan(stage)
            self.assertEqual(len(plan), count)
            run.validate_plan(plan)
            self.assertEqual(plan, run.make_plan(stage))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            run.validate_plan(run.make_plan("smoke") * 2)
        self.assertEqual(len(run.make_plan("B3", models=["luna", "oss"])), 20)
        for v in run.BENIGN_VARIANTS:
            prompt = run.build_prompt(v, "alice", "bob", "n-test-alice", "n-test-bob", 1, "n-test-pkg", "H3", 60)
            self.assertNotIn("peer", prompt.lower())
            self.assertNotIn('"peer_value":7', prompt)

    def test_provision_timeout_still_cleans_model(self):
        clean = {"absence_proved": True, "containers_readback": [], "networks_readback": [],
                 "art_a_cohort_networks": [], "readback_returncodes": {"docker_ps": 0, "network_ls": 0, "art_a_inspect": 0}}
        for proven in (True, False):
            with self.subTest(proven=proven), tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                clean["absence_proved"] = proven
                def service(command, *args, **kwargs):
                    if command == "provision":
                        raise subprocess.TimeoutExpired("ssh provision", 180)
                    self.assertEqual(command, "cleanup")
                    return dict(clean)
                with patch.object(run, "DATA", d), patch.object(run, "STOP_PATH", d / "stop.json"), patch.object(run, "STOP_EVENT", threading.Event()), patch.object(run, "service", side_effect=service) as svc, patch.object(run, "stop_launches") as stop:
                    run.run_cohort(run.make_plan("smoke")[0], 10, run.Ledger(d / "ledger.jsonl"), None)
                    self.assertEqual([c.args[0] for c in svc.call_args_list], ["provision", "cleanup"])
                    self.assertEqual(stop.call_count, 0 if proven else 1)
                    self.assertEqual(run.STOP_EVENT.is_set(), not proven)
                summary = json.loads((d / "smk-fix-01/summary.json").read_text())
                self.assertEqual(summary["outcome"], "infra_error")

    def test_provision_timeout_still_cleans_scripted(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            def service(command, *args, **kwargs):
                if command == "provision":
                    raise subprocess.TimeoutExpired("ssh provision", 180)
                self.assertEqual(command, "cleanup")
                return {"absence_proved": True}
            import random
            with patch.object(scripted, "DATA", d), patch.object(scripted, "STOP_PATH", d / "stop.json"), patch.object(scripted, "service", side_effect=service) as svc:
                with self.assertRaises(subprocess.TimeoutExpired):
                    scripted.run_trial("open", 0, random.Random(1))
            self.assertEqual([c.args[0] for c in svc.call_args_list], ["provision", "cleanup"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
