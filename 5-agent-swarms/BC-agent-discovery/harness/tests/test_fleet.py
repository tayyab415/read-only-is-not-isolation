import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _path  # noqa: F401
import fleet
import stub_fixtures
import stub_prompts
from ledger import Ledger
from models import FakeModel
from node import FakeNode

SCRIPT = [[{"name": "bash", "arguments": {"command": "echo hi > out.txt"}}],
          [{"name": "bash", "arguments": {"command": "cat out.txt"}}],
          [{"name": "finish", "arguments": {"summary": "ok"}}]]


def sched():
    rows = []
    for i, infra in enumerate(["shared", "independent", "independent", "shared", "shared", "independent"]):
        rows.append({"episode_id": f"c-{i:02d}", "study": "C" if i < 4 else "B", "block": f"X{i // 3}",
                     "cell": {"pairing": "glm-deepseek", "workload": "modest", "infra": infra},
                     "roles": {"A": "glm", "B": "deepseek"} if i < 4 else {"sender": "glm", "receiver": "deepseek"}})
    return rows


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "data"
        self.ledger = Ledger(Path(self.tmp.name) / "l.jsonl", cap=5.0)
        self.nodes = {n: FakeNode(n, exec_delay=0.05) for n in ("n01", "n02", "n03")}
        self.model = FakeModel(SCRIPT, self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, rows=None, **kw):
        return fleet.run_fleet(rows or sched(), self.nodes, self.out, self.model, self.ledger,
                               stub_prompts, stub_fixtures, max_parallel=3, poll_s=0.01, **kw)

    def progress(self):
        return [json.loads(l) for l in (self.out / "progress.jsonl").read_text().splitlines()]

    def test_six_episodes_three_nodes_no_double_booking_then_resume(self):
        summary = self.go()
        self.assertEqual(summary["launched"], 6)
        self.assertEqual(summary["statuses"], {"complete": 6})
        for n in self.nodes.values():
            self.assertEqual(n.violations, [], n.name)
            self.assertEqual(n.status()["containers"], [])
        for r in sched():
            ep = json.loads((self.out / r["study"] / r["episode_id"] / "episode.json").read_text())
            self.assertEqual(ep["status"], "complete")
            used = set(ep["nodes"].values())
            self.assertEqual(len(used), 1 if r["cell"]["infra"] == "shared" else 2)
        # shared episodes put both actors on one node, slots 1 and 2
        slots = {(h[1], h[2]) for n in self.nodes.values() for h in n.history if h[0] == "setup" and h[1] == "c-00"}
        self.assertEqual(slots, {("c-00", 1), ("c-00", 2)})
        events = [p["event"] for p in self.progress()]
        self.assertEqual(events.count("launch"), 6)
        self.assertEqual(events.count("done"), 6)
        # more than one episode ran at the same time
        self.assertTrue(self.max_overlap() >= 2)

        calls_before = len(self.model.requests)
        again = self.go()
        self.assertEqual(again["launched"], 0)
        self.assertEqual(again["skipped"], 6)
        self.assertEqual(len(self.model.requests), calls_before)

    def max_overlap(self):
        live, best = set(), 0
        for p in self.progress():
            if p["event"] == "launch":
                live.add(p["episode_id"])
            elif p["event"] == "done":
                live.discard(p["episode_id"])
            best = max(best, len(live))
        return best

    def test_resume_moves_aside_partial_dir(self):
        rows = sched()[:1]
        part = self.out / "C" / "c-00"
        part.mkdir(parents=True)
        (part / "actor-A.jsonl").write_text("{}\n")
        summary = self.go(rows)
        self.assertEqual(summary["statuses"], {"complete": 1})
        self.assertTrue(any(p.name.startswith("c-00.partial-") for p in (self.out / "C").iterdir()))

    def test_stop_file_prevents_launch(self):
        self.out.mkdir(parents=True)
        (self.out / "STOP").write_text("")
        summary = self.go()
        self.assertEqual(summary["launched"], 0)
        self.assertEqual(summary["stopped"], "stop_file")

    def test_stop_at_utc_in_past(self):
        summary = self.go(stop_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(summary["launched"], 0)
        self.assertEqual(summary["stopped"], "stop_at_utc")

    def test_stop_file_mid_run_lets_inflight_finish(self):
        self.out.mkdir(parents=True)
        threading.Timer(0.12, lambda: (self.out / "STOP").write_text("")).start()
        summary = self.go()
        self.assertGreater(summary["launched"], 0)
        self.assertLess(summary["launched"], 6)
        self.assertEqual(summary["stopped"], "stop_file")
        self.assertEqual(summary["statuses"], {"complete": summary["launched"]})

    def test_budget_guard_stops_launch(self):
        summary = self.go(episode_max_usd=10.0)
        self.assertEqual(summary["launched"], 0)
        self.assertEqual(summary["stopped"], "budget")

    def test_only_blocks(self):
        summary = self.go(only_blocks=["X1"])
        self.assertEqual(summary["launched"], 3)
        self.assertEqual({p["block"] for p in self.progress() if p["event"] == "launch"}, {"X1"})

    def test_failing_node_is_quarantined(self):
        self.nodes["n01"].fail_on = {"setup": True}
        rows = [r for r in sched() if r["cell"]["infra"] == "shared"][:3]
        summary = self.go(rows)
        self.assertIn("n01", summary["quarantined"])
        self.assertEqual(summary["launched"] + summary["not_run"], 3)

    def test_load_rows_validates(self):
        p = Path(self.tmp.name) / "s.json"
        p.write_text(json.dumps(sched()))
        self.assertEqual(len(fleet.load_rows(p)), 6)
        bad = sched()
        bad[1]["cell"]["infra"] = "weird"
        p.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            fleet.load_rows(p)

    def test_cli_fake(self):
        sp, npth = Path(self.tmp.name) / "s.json", Path(self.tmp.name) / "n.json"
        sp.write_text(json.dumps(sched()))
        npth.write_text(json.dumps([{"name": f"n0{i}", "ssh": f"bc-n0{i}"} for i in (1, 2, 3)]))
        out = Path(self.tmp.name) / "cli-data"
        rc = fleet.main(["--schedule", str(sp), "--nodes", str(npth), "--out", str(out),
                         "--max-parallel", "3", "--fake"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(list(out.glob("*/*/episode.json"))), 6)


if __name__ == "__main__":
    unittest.main()
