"""Run the real harness (FakeNode + FakeModel, no network) and audit what it writes."""
import sys
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
from common import BC_DIR
from verify_episode import verify

HARNESS = BC_DIR / "harness"
try:
    sys.path.insert(0, str(HARNESS))
    sys.path.insert(0, str(HARNESS / "tests"))
    import episode
    import stub_fixtures
    import stub_prompts
    from ledger import Ledger
    from models import FakeFail, FakeModel
    from node import FakeNode
    HAVE = True
except Exception:  # harness missing or its dependencies (requests) not installed
    HAVE = False

BASH = lambda c, **k: [{"name": "bash", "arguments": {"command": c, **k}}]  # noqa: E731
FINISH = [{"name": "finish", "arguments": {"summary": "done"}}]


@unittest.skipUnless(HAVE, "harness not importable")
class HarnessCompat(unittest.TestCase):
    def run_ep(self, script, infra="shared", ep="c-compat"):
        tmp = Path(tempfile.mkdtemp())
        led = Ledger(tmp / "l.jsonl", cap=5.0)
        nodes = {"n1": FakeNode("n1"), "n2": FakeNode("n2")}
        row = {"episode_id": ep, "study": "C", "block": "C1", "cell": {"pairing": "glm-deepseek", "workload": "modest", "infra": infra},
               "roles": {"A": "glm", "B": "deepseek"}}
        asg = {"A": {"node": "n1", "slot": 1}, "B": {"node": "n1", "slot": 2}} if infra == "shared" else \
            {"A": {"node": "n1", "slot": 1}, "B": {"node": "n2", "slot": 1}}
        res = episode.run_episode(row, asg, nodes, FakeModel(script, led), led, tmp / "data", stub_prompts, stub_fixtures)
        return Path(res["dir"]), tmp / "l.jsonl", res

    def test_clean_episode_passes(self):
        for infra in ("shared", "independent"):
            with self.subTest(infra=infra):
                d, led, res = self.run_ep({"A": [BASH("echo hi"), "no tool call this time", BASH("ls"), FINISH],
                                           "B": [BASH("echo yo"), FINISH]}, infra)
                self.assertEqual(res["status"], "complete")
                r = verify(d, led)
                self.assertTrue(r["ok"], r["failures"])

    def test_error_tool_rows_and_calls_after_finish(self):
        script = {"A": [[{"name": "bash", "arguments": "not json"}, {"name": "nope", "arguments": {}}],
                        BASH("echo ok"), FINISH + BASH("echo never")],
                  "B": [FINISH]}
        d, led, res = self.run_ep(script)
        r = verify(d, led)
        self.assertTrue(r["ok"], r["failures"])

    def test_provider_failure_episode_is_still_consistent(self):
        d, led, res = self.run_ep({"A": [BASH("echo a"), FakeFail(400)], "B": [FINISH]})
        self.assertEqual(res["status"], "provider_failure")
        r = verify(d, led)
        self.assertTrue(r["ok"], r["failures"])

    def test_timeup_message_from_the_harness_is_accepted(self):
        d, led, res = self.run_ep({"A": [BASH("echo %d" % i) for i in range(11)] + [FINISH], "B": [FINISH]})
        r = verify(d, led)
        self.assertTrue(r["ok"], r["failures"])


if __name__ == "__main__":
    unittest.main()
