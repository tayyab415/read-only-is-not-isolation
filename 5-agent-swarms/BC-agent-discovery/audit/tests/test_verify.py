import json
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
import make_synthetic as ms
from verify_episode import verify, verify_all


class VerifyValid(unittest.TestCase):
    def test_valid_episodes_pass(self):
        for study in "CB":
            for infra in ("shared", "independent"):
                with self.subTest(study=study, infra=infra):
                    tmp = Path(tempfile.mkdtemp())
                    led = tmp / "l.jsonl"
                    d = ms.make_episode(tmp, led, study=study, infra=infra)
                    r = verify(d, led, secrets=["NOT-IN-ANY-FILE"])
                    self.assertTrue(r["ok"], r["failures"])
                    self.assertTrue(all(c["passed"] for c in r["checks"].values()))

    def test_reports_401_and_retry_and_render_source(self):
        tmp = Path(tempfile.mkdtemp())
        led = tmp / "l.jsonl"
        d = ms.make_episode(tmp, led)
        r = verify(d, led)
        self.assertEqual(r["checks"]["gateway"]["n_401"], 1)
        self.assertEqual(r["checks"]["gateway"]["n_401_by_slot"], {"2": 1})
        self.assertIn(r["checks"]["history"]["render_source"], ("harness/render.py", "audit/_render_fallback.py"))
        self.assertEqual(r["checks"]["ledger"]["n_request_rows"], 7)  # includes the 429 retry attempt
        self.assertEqual(r["checks"]["ledger"]["n_200_responses"], 6)

    def test_missing_files_fail(self):
        tmp = Path(tempfile.mkdtemp())
        led = tmp / "l.jsonl"
        d = ms.make_episode(tmp, led)
        (d / "check.json").unlink()
        (d / "workspace-B.tar.gz").unlink()
        codes = {f["code"] for f in verify(d, led)["failures"]}
        self.assertIn("file_missing", codes)

    def test_non_complete_episode_needs_fewer_files(self):
        tmp = Path(tempfile.mkdtemp())
        led = tmp / "l.jsonl"
        d = ms.make_episode(tmp, led)
        ep = json.loads((d / "episode.json").read_text())
        ep["status"] = "infra_failure"
        ep["stop_reasons"]["B"] = "infra_failure"
        (d / "episode.json").write_text(json.dumps(ep))
        (d / "check.json").unlink()
        r = verify(d, led)
        self.assertNotIn("file_missing", {f["code"] for f in r["failures"]})

    def test_cross_episode_duplicate_call_key(self):
        tmp = Path(tempfile.mkdtemp())
        led = tmp / "l.jsonl"
        ms.make_episode(tmp, led, ep_id="C-test-001")
        d2 = ms.make_episode(tmp, led, ep_id="C-test-002")
        rows = [json.loads(l) for l in (d2 / "actor-A.jsonl").read_text().splitlines()]
        rows[0]["call_key"] = "C-test-001/A/1/1"
        (d2 / "actor-A.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        res = verify_all(tmp, led)
        self.assertTrue(res["C-test-001"]["ok"] is False)
        self.assertIn("duplicate_call_key_across_episodes", {f["code"] for f in res["C-test-002"]["failures"]})

    def test_auditor_failure_on_garbage_is_a_failure(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "C" / "junk").mkdir(parents=True)
        (tmp / "C" / "junk" / "spec.json").write_text("{not json")
        res = verify_all(tmp, tmp / "none.jsonl")
        self.assertFalse(res["junk"]["ok"])


if __name__ == "__main__":
    unittest.main()
