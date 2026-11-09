import json
import re
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
import analyze
import blind_packets
import make_synthetic as ms
import score
from corruption_tests import altered_tool_message_in_history
from verify_episode import verify_all


def build_corpus():
    tmp = Path(tempfile.mkdtemp())
    led = tmp / "led.jsonl"
    data = tmp / "data"
    n = 0
    for infra in ("shared", "independent"):
        for i in range(10):
            n += 1
            ms.make_episode(data, led, ep_id=f"C-{n:03d}", study="C", infra=infra,
                            a_text="Maybe another user is on the server." if (infra, i) == ("shared", 0) else "fine")
    for infra, guess in (("shared", 5), ("shared", 7), ("independent", 5), ("independent", 1)):
        n += 1
        ms.make_episode(data, led, ep_id=f"B-{n:03d}", study="B", infra=infra, value=5, guess=guess)
    return tmp, led, data


class BlindAndAnalyze(unittest.TestCase):
    def test_blind_packets(self):
        tmp, led, data = build_corpus()
        rows = score.score_data_dir(data)
        sp = tmp / "scores.jsonl"
        sp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        key = blind_packets.build(data, sp, tmp / "blind")
        flagged = [p for p in key["packets"].values() if p["reason"] == "flagged"]
        random_ = [p for p in key["packets"].values() if p["reason"] == "random"]
        self.assertIn("C-001", {p["episode_id"] for p in flagged})
        self.assertEqual(len(random_), -(-15 * key["meta"]["n_unflagged_complete"] // 100))
        again = blind_packets.build(data, sp, tmp / "blind2")
        self.assertEqual(set(again["packets"]), set(key["packets"]))  # seeded, reproducible
        for pid, meta in key["packets"].items():
            text = (tmp / "blind" / f"{pid}.md").read_text()
            self.assertIn("Blind review rubric (frozen)", text)
            self.assertNotIn(meta["episode_id"], text)
            self.assertNotRegex(text.replace("qwen2.5-1.5b-instruct", ""), re.compile(r"qwen|llama|deepinfra|novita|no_think", re.I))
            self.assertIn("## Agent 1", text)
            self.assertIn("## Agent 2", text)
            self.assertIn("tool result", text)
            self.assertNotIn("ten-", text)
        k = json.loads((tmp / "blind" / "key.json").read_text())
        self.assertEqual(k["meta"]["rubric_sha256"], blind_packets.RUBRIC_SHA)
        pid = next(iter(k["packets"]))
        self.assertEqual(sorted(k["packets"][pid]["agents"].values()), ["A", "B"])

    def test_analyze_denominators_and_tables(self):
        tmp, led, data = build_corpus()
        # corrupt one complete episode (audit failure) and fail one for the provider
        altered_tool_message_in_history(data / "C" / "C-002", led)
        ep = json.loads((data / "C" / "C-003" / "episode.json").read_text())
        ep["status"], ep["stop_reasons"]["A"] = "provider_failure", "provider_failure"
        (data / "C" / "C-003" / "episode.json").write_text(json.dumps(ep))
        rows = score.score_data_dir(data)
        (tmp / "scores.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        ver = verify_all(data, led)
        (tmp / "verification.json").write_text(json.dumps({"episodes": ver}))
        res = analyze.analyze(data, tmp / "scores.jsonl", tmp / "verification.json")
        self.assertEqual(res["n_episodes_found"], 24)
        self.assertEqual(res["n_included"], 22)
        self.assertEqual(res["failures_by_cause"], {"audit": 1, "provider": 1})
        c = res["summary"]["C"]
        self.assertEqual(c["POOLED"]["n_episodes"], 18)
        self.assertEqual(c["POOLED"]["c2"]["k"], 1)  # candidate, unreviewed
        self.assertEqual(c["POOLED"]["c3"]["k"], 0)
        self.assertAlmostEqual(c["POOLED"]["c3"]["upper95_one_sided"], 1 - 0.05 ** (1 / 18), places=9)
        self.assertEqual(c["POOLED"]["c1"]["k"], 18)
        b = res["summary"]["B"]
        self.assertEqual((b["POOLED"]["recovered"]["k"], b["POOLED"]["recovered"]["n"]), (2, 4))
        self.assertAlmostEqual(b["POOLED"]["recovered"]["p_vs_chance_1_16"], 1 - (15 / 16) ** 4 - 4 * (1 / 16) * (15 / 16) ** 3, places=9)
        f = res["summary"]["B_shared_vs_independent_fisher"]
        self.assertAlmostEqual(f["p_two_sided"], 1.0, places=9)
        md = analyze.tables_md(res)
        self.assertIn("Failures by cause", md)
        self.assertIn("unreviewed", md)

    def test_reviews_replace_candidates(self):
        tmp, led, data = build_corpus()
        rows = score.score_data_dir(data)
        (tmp / "scores.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (tmp / "verification.json").write_text(json.dumps({"episodes": verify_all(data, led)}))
        (tmp / "reviews.json").write_text(json.dumps({r["episode_id"]: {"c2": False, "c3": False, "c4": False}
                                                      for r in rows if r["study"] == "C"}))
        res = analyze.analyze(data, tmp / "scores.jsonl", tmp / "verification.json", tmp / "reviews.json")
        self.assertEqual(res["summary"]["C"]["POOLED"]["c2"]["k"], 0)
        self.assertIn("confirmed", res["c_outcome_basis"])

    def test_missing_verification_is_an_audit_failure(self):
        tmp, led, data = build_corpus()
        rows = score.score_data_dir(data)
        (tmp / "scores.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (tmp / "verification.json").write_text(json.dumps({"episodes": {}}))
        res = analyze.analyze(data, tmp / "scores.jsonl", tmp / "verification.json")
        self.assertEqual(res["n_included"], 0)
        self.assertEqual(res["failures_by_cause"], {"audit": 24})


if __name__ == "__main__":
    unittest.main()
