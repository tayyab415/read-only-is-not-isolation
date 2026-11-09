import json
import tempfile
import unittest
from pathlib import Path

import requests

import _path  # noqa: F401
from ledger import Ledger
from models import (BudgetExceeded, FakeFail, FakeModel, HFClient, ProviderFailure,
                    assert_no_token, build_body)

TOKEN = "hf_" + "TESTTOKEN" * 3 + "1234"  # dummy value, built from parts so secret scanners do not flag it
META = {"episode": "e1", "actor": "A", "turn": 1}
TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
      "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


class Resp:
    def __init__(self, status, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Seq:
    """post() stub returning scripted outcomes; exceptions are raised."""
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "l.jsonl", cap=1.0)
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, outcomes):
        self.post = Seq(outcomes)
        return HFClient(TOKEN, self.ledger, post=self.post, sleep=self.sleeps.append)

    def test_body_exact(self):
        body = build_body("glm", MSGS, TOOLS)
        self.assertEqual(body, {"model": "zai-org/GLM-5.3-Flash:deepinfra", "messages": MSGS, "tools": TOOLS,
                                "tool_choice": "auto", "temperature": 0.7, "top_p": 0.9, "max_tokens": 2048})
        self.assertEqual(build_body("deepseek", MSGS, TOOLS)["model"],
                         "deepseek-ai/DeepSeek-V4-Flash:deepinfra")

    def test_success_records_actual(self):
        c = self.client([Resp(200, OK)])
        resp, attempts = c.call("deepseek", MSGS, TOOLS, META)
        self.assertEqual(resp, OK)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["call_key"], "e1/A/1/1")
        self.assertIsNotNone(attempts[0]["actual_usd"])
        self.assertAlmostEqual(self.ledger.total(), attempts[0]["actual_usd"])
        self.assertEqual(self.post.calls[0]["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_retries_429_then_ok_with_backoff(self):
        c = self.client([Resp(429, {"e": 1}), requests.ConnectionError("net"), Resp(503, {"e": 2}), Resp(200, OK)])
        resp, attempts = c.call("glm", MSGS, TOOLS, META)
        self.assertEqual([a["attempt"] for a in attempts], [1, 2, 3, 4])
        self.assertEqual(self.sleeps, [2, 4, 8])
        rows = [json.loads(l) for l in (Path(self.tmp.name) / "l.jsonl").read_text().splitlines()]
        self.assertEqual(sum(r["kind"] == "reserve" for r in rows), 4)
        self.assertEqual(sum(r["kind"] == "actual" for r in rows), 1)
        # failed attempts keep their reservations
        self.assertGreater(self.ledger.total(), attempts[3]["actual_usd"])

    def test_retries_exhausted(self):
        c = self.client([Resp(500, {})] * 4)
        with self.assertRaises(ProviderFailure) as cm:
            c.call("glm", MSGS, TOOLS, META)
        self.assertEqual(len(cm.exception.attempts), 4)
        self.assertEqual(self.sleeps, [2, 4, 8])

    def test_other_4xx_no_retry(self):
        c = self.client([Resp(400, {"error": "bad"})])
        with self.assertRaises(ProviderFailure) as cm:
            c.call("glm", MSGS, TOOLS, META)
        self.assertEqual(cm.exception.http_status, 400)
        self.assertEqual(len(cm.exception.attempts), 1)
        self.assertEqual(self.sleeps, [])

    def test_budget_refusal(self):
        self.ledger.cap = 1e-9
        c = self.client([Resp(200, OK)])
        with self.assertRaises(BudgetExceeded):
            c.call("glm", MSGS, TOOLS, META)
        self.assertEqual(self.post.calls, [])

    def test_token_not_in_repr_or_errors(self):
        c = self.client([requests.ConnectionError(f"failed with {TOKEN}")] * 4)
        self.assertNotIn(TOKEN, repr(c))
        with self.assertRaises(ProviderFailure) as cm:
            c.call("glm", MSGS, TOOLS, META)
        self.assertNotIn(TOKEN, json.dumps(cm.exception.attempts, default=str))

    def test_assert_no_token(self):
        d = Path(self.tmp.name) / "out"
        d.mkdir()
        (d / "a.json").write_text("clean")
        assert_no_token(d, TOKEN)
        (d / "b.txt").write_text(f"oops {TOKEN}")
        with self.assertRaises(AssertionError) as cm:
            assert_no_token(d, TOKEN)
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertIn("b.txt", str(cm.exception))


class FakeModelTests(unittest.TestCase):
    def test_script_and_fail(self):
        fm = FakeModel({"A": [[{"name": "bash", "arguments": {"command": "ls"}}], "just text", FakeFail(400)]})
        r, a = fm.call("glm", MSGS, TOOLS, {"episode": "e", "actor": "A", "turn": 1})
        tc = r["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(tc["function"]["name"], "bash")
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"command": "ls"})
        r, a = fm.call("glm", MSGS, TOOLS, {"episode": "e", "actor": "A", "turn": 2})
        self.assertNotIn("tool_calls", r["choices"][0]["message"])
        with self.assertRaises(ProviderFailure):
            fm.call("glm", MSGS, TOOLS, {"episode": "e", "actor": "A", "turn": 3})
        self.assertEqual(len(fm.requests), 3)


if __name__ == "__main__":
    unittest.main()
