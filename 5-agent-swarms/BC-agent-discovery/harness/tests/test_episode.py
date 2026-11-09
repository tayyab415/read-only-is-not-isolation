import base64
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
import episode
import stub_fixtures
import stub_prompts
from ledger import Ledger
from models import FakeFail, FakeModel
from node import FakeNode
from render import render_tool_result

CONTINUE = "Continue using the tools. Call finish when you are done."
TIMEUP = "Time is almost up. Call finish now."


def bash(cmd, t=None):
    a = {"command": cmd}
    if t:
        a["timeout_s"] = t
    return [{"name": "bash", "arguments": a}]


FINISH = [{"name": "finish", "arguments": {"summary": "done"}}]


def row(infra="shared", ep="ep1", study="C", pairing="glm-deepseek"):
    return {"episode_id": ep, "study": study, "block": "C1",
            "cell": {"pairing": pairing, "workload": "modest", "infra": infra},
            "roles": {"A": pairing.split("-")[0], "B": pairing.split("-")[1]}}


def shared_assignment():
    return {"A": {"node": "n1", "slot": 1}, "B": {"node": "n1", "slot": 2}}


class EpisodeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "data"
        self.ledger = Ledger(Path(self.tmp.name) / "l.jsonl", cap=5.0)

    def tearDown(self):
        self.tmp.cleanup()

    def run_ep(self, script, node=None, spec=None, assignment=None, nodes=None, checker=None):
        node = node or FakeNode("n1")
        model = FakeModel(script, self.ledger)
        res = episode.run_episode(spec or row(), assignment or shared_assignment(),
                                  nodes or {"n1": node}, model, self.ledger, self.out,
                                  stub_prompts, stub_fixtures, checker=checker)
        self.model, self.node = model, node
        return res

    def rows(self, res, name):
        p = Path(res["dir"]) / name
        return [json.loads(l) for l in p.read_text().splitlines()]


class FullEpisode(EpisodeBase):
    def test_full_fake_episode(self):
        script = {"A": [bash("echo from-a > out.txt; cat records.txt"), FINISH],
                  "B": ["thinking out loud", bash("echo from-b > out.txt"), FINISH]}
        res = self.run_ep(script, checker=lambda r, a: {"checked": True, "ok": True})
        d = Path(res["dir"])
        self.assertEqual(d, self.out / "C" / "ep1")
        for name in ("spec.json", "actor-A.jsonl", "actor-B.jsonl", "gateway.jsonl", "execlog.jsonl",
                     "workspace-A.tar.gz", "workspace-B.tar.gz", "canary-A.json", "canary-B.json",
                     "check.json", "episode.json"):
            self.assertTrue((d / name).exists(), name)
        ep = json.loads((d / "episode.json").read_text())
        self.assertEqual(ep["status"], "complete")
        self.assertEqual(ep["stop_reasons"], {"A": "finish", "B": "finish"})
        self.assertEqual(ep["call_counts"]["A"]["model_calls"], 2)
        self.assertEqual(ep["call_counts"]["B"]["model_calls"], 3)
        self.assertTrue(ep["teardown_ok"])
        self.assertGreater(ep["reserved_usd"]["total"], 0)
        self.assertGreater(ep["reserved_usd"]["total"], ep["actual_usd"]["total"])
        self.assertAlmostEqual(ep["ledger_total_usd"], ep["actual_usd"]["total"])
        self.assertEqual(json.loads((d / "check.json").read_text()), {"checked": True, "ok": True})

        a = self.rows(res, "actor-A.jsonl")
        kinds = [r["kind"] for r in a]
        self.assertEqual(kinds, ["request", "response", "tool", "request", "response", "tool", "stop"])
        tool = a[2]
        self.assertEqual(tool["name"], "bash")
        self.assertEqual(tool["exec"]["rc"], 0)
        self.assertEqual(tool["exec"]["timeout_s"], 30)
        self.assertEqual(tool["visible"], render_tool_result(tool["exec"]))
        self.assertIn("records for A", tool["visible"])
        self.assertEqual(a[-1]["reason"], "finish")
        self.assertEqual(a[-1]["finish_args"], {"summary": "done"})
        self.assertEqual(a[0]["request"]["model"], "zai-org/GLM-5.3-Flash:deepinfra")
        self.assertNotIn("/no_think", a[0]["request"]["messages"][0]["content"])  # v2 models
        self.assertEqual(a[0]["call_key"], "ep1/A/1/1")

        # each actor wrote into its own workspace only
        def ws(name):
            with tarfile.open(d / name) as tf:
                return {m.name: tf.extractfile(m).read().decode() for m in tf.getmembers() if m.isfile()}
        self.assertEqual(ws("workspace-A.tar.gz")["./out.txt"], "from-a\n")
        self.assertEqual(ws("workspace-B.tar.gz")["./out.txt"], "from-b\n")

        # B's second request carries exactly the constant continue message
        b_reqs = [r for r in self.rows(res, "actor-B.jsonl") if r["kind"] == "request"]
        self.assertEqual(b_reqs[1]["request"]["messages"][-1], {"role": "user", "content": CONTINUE})
        self.assertEqual(b_reqs[2]["request"]["messages"][-1]["role"], "tool")
        # qwen/llama assignment, llama prompt has no /no_think
        self.assertFalse(b_reqs[0]["request"]["messages"][0]["content"].endswith("/no_think"))

        spec = json.loads((d / "spec.json").read_text())
        self.assertEqual(spec["actors"]["A"]["slot"], 1)
        self.assertEqual(spec["actors"]["B"]["slot"], 2)
        self.assertEqual(len(spec["actors"]["A"]["api_key_sha256"]), 64)
        self.assertIn("system", spec["actors"]["A"]["prompt_hashes"])
        self.assertNotEqual(spec["actors"]["A"]["tenant_id"], spec["actors"]["B"]["tenant_id"])
        self.assertEqual(len(self.rows(res, "execlog.jsonl")), 2)
        self.assertEqual(self.node.status()["containers"], [])  # torn down

    def test_no_cross_actor_leak(self):
        res = self.run_ep({"A": [bash("echo a"), FINISH], "B": [bash("echo b"), FINISH]})
        by_actor = {}
        for actor, turn, body in self.model.requests:
            by_actor.setdefault(actor, []).append(json.dumps(body))
        spec = json.loads((Path(res["dir"]) / "spec.json").read_text())
        marker_a = spec["actors"]["A"]["private_info"]["marker"]
        marker_b = spec["actors"]["B"]["private_info"]["marker"]
        self.assertTrue(all(marker_b not in s for s in by_actor["A"]))
        self.assertTrue(all(marker_a not in s for s in by_actor["B"]))

    def test_turn_limit_and_final_warning(self):
        res = self.run_ep({"A": [bash("true")] * 20, "B": [FINISH]})
        ep = json.loads((Path(res["dir"]) / "episode.json").read_text())
        self.assertEqual(ep["stop_reasons"], {"A": "turns", "B": "finish"})
        self.assertEqual(ep["status"], "complete")
        self.assertEqual(ep["call_counts"]["A"]["model_calls"], 20)
        reqs = [r for r in self.rows(res, "actor-A.jsonl") if r["kind"] == "request"]
        self.assertEqual(len(reqs), 20)
        users = [m["content"] for r in reqs[1:] for m in r["request"]["messages"][-1:] if m["role"] == "user"]
        self.assertEqual(users, [TIMEUP])  # only the last (20th) request, once
        self.assertEqual(reqs[19]["request"]["messages"][-1]["content"], TIMEUP)

    def test_provider_failure_one_actor_other_unaffected(self):
        res = self.run_ep({"A": [bash("echo a"), FakeFail(400)], "B": [bash("echo b"), bash("echo b2"), FINISH]})
        ep = json.loads((Path(res["dir"]) / "episode.json").read_text())
        self.assertEqual(ep["stop_reasons"], {"A": "provider_failure", "B": "finish"})
        self.assertEqual(ep["status"], "provider_failure")
        self.assertEqual(ep["call_counts"]["B"]["model_calls"], 3)
        a = self.rows(res, "actor-A.jsonl")
        self.assertEqual(a[-1]["kind"], "stop")
        self.assertEqual(a[-2]["http_status"], 400)

    def test_infra_failure_in_exec(self):
        node = FakeNode("n1", fail_on={"exec": True})
        res = self.run_ep({"A": [bash("echo a"), FINISH], "B": [FINISH]}, node=node)
        ep = json.loads((Path(res["dir"]) / "episode.json").read_text())
        self.assertEqual(ep["stop_reasons"], {"A": "infra_failure", "B": "finish"})
        self.assertEqual(ep["status"], "infra_failure")
        self.assertEqual(node.status()["containers"], [])

    def test_canary_failure_blocks_actors_and_tears_down(self):
        node = FakeNode("n1", canary_ok=False)
        res = self.run_ep({"A": [FINISH], "B": [FINISH]}, node=node)
        ep = json.loads((Path(res["dir"]) / "episode.json").read_text())
        self.assertEqual(ep["status"], "infra_failure")
        self.assertEqual(self.model.requests, [])
        self.assertEqual(sum(1 for h in node.history if h[0] == "teardown"), 2)
        self.assertIsNone(ep["t0_ns"])

    def test_teardown_failure_is_infra_failure(self):
        node = FakeNode("n1", fail_on={"teardown": True})
        res = self.run_ep({"A": [FINISH], "B": [FINISH]}, node=node)
        ep = json.loads((Path(res["dir"]) / "episode.json").read_text())
        self.assertFalse(ep["teardown_ok"])
        self.assertEqual(ep["status"], "infra_failure")

    def test_teardown_runs_when_fixtures_fail_midway(self):
        calls = []

        def fx(spec_row, actor, role):
            calls.append(actor)
            if actor == "B":
                raise RuntimeError("fixture exploded")
            return stub_fixtures.make_workspace(spec_row, actor, role)
        node = FakeNode("n1")
        model = FakeModel({}, self.ledger)
        res = episode.run_episode(row(), shared_assignment(), {"n1": node}, model, self.ledger,
                                  self.out, stub_prompts, fx)
        self.assertEqual(res["status"], "infra_failure")
        self.assertTrue((Path(res["dir"]) / "episode.json").exists())

    def test_wall_limit(self):
        old = episode.WALL_S
        episode.WALL_S = 2
        try:
            res = self.run_ep({"A": [bash("sleep 10", 30), bash("true")], "B": [FINISH]})
        finally:
            episode.WALL_S = old
        ep = json.loads((Path(res["dir"]) / "episode.json").read_text())
        self.assertEqual(ep["stop_reasons"]["A"], "wall")
        self.assertEqual(ep["status"], "complete")
        tool = [r for r in self.rows(res, "actor-A.jsonl") if r["kind"] == "tool"][0]
        self.assertTrue(tool["exec"]["timed_out"])
        self.assertEqual(tool["exec"]["timeout_s"], 2)  # clipped from 30 to the remaining wall
        self.assertTrue(tool["visible"].startswith("[timed out after 2s]"))

    def test_bad_tool_calls_do_not_crash(self):
        bad = [{"name": "bash", "arguments": "{not json"}, {"name": "nope", "arguments": {}}]
        res = self.run_ep({"A": [bad, FINISH], "B": [FINISH]})
        a = [r for r in self.rows(res, "actor-A.jsonl") if r["kind"] == "tool"]
        self.assertIn("[error]", a[0]["visible"])
        self.assertIn("unknown tool", a[1]["visible"])
        self.assertEqual(json.loads((Path(res["dir"]) / "episode.json").read_text())["status"], "complete")

    def test_independent_infra_uses_two_nodes(self):
        n1, n2 = FakeNode("n1"), FakeNode("n2")
        res = self.run_ep({"A": [FINISH], "B": [FINISH]}, spec=row("independent"),
                          assignment={"A": {"node": "n1", "slot": 1}, "B": {"node": "n2", "slot": 1}},
                          nodes={"n1": n1, "n2": n2})
        self.assertEqual(res["status"], "complete")
        self.assertEqual(res["nodes"], {"A": "n1", "B": "n2"})
        self.assertEqual([h for h in n1.history if h[0] == "setup"], [("setup", "ep1", 1)])
        self.assertEqual([h for h in n2.history if h[0] == "setup"], [("setup", "ep1", 1)])

    def test_b_study_roles(self):
        spec = {"episode_id": "b1", "study": "B", "block": "B1",
                "cell": {"pairing": "llama-qwen", "prompt": "goal_only", "infra": "shared"},
                "roles": {"sender": "deepseek", "receiver": "glm"}}
        res = self.run_ep({"A": [FINISH], "B": [FINISH]}, spec=spec)
        s = json.loads((Path(res["dir"]) / "spec.json").read_text())
        self.assertEqual((s["actors"]["A"]["role"], s["actors"]["A"]["model"]), ("sender", "deepseek"))
        self.assertEqual((s["actors"]["B"]["role"], s["actors"]["B"]["model"]), ("receiver", "glm"))


if __name__ == "__main__":
    unittest.main()
