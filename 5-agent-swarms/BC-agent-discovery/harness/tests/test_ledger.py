import json
import tempfile
import threading
import unittest
from pathlib import Path

import _path  # noqa: F401
from ledger import Ledger, actual_usd, reservation_usd


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "l.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def kw(self, i=0):
        return dict(episode="e", actor="A", turn=i, model="glm")

    def test_formula(self):
        req = {"messages": ["x" * 100]}
        n = len(json.dumps(req).encode())
        self.assertAlmostEqual(reservation_usd("deepseek", req),
                               1.2 * (n / 2 * 0.09 + 2048 * 0.18) / 1e6)
        self.assertAlmostEqual(actual_usd("glm", {"prompt_tokens": 1000, "completion_tokens": 200}),
                               1.2 * (1000 * 0.15 + 200 * 0.50) / 1e6)

    def test_last_row_per_call_key(self):
        led = Ledger(self.path, cap=1.0)
        self.assertTrue(led.reserve("k1", 0.5, **self.kw()))
        self.assertAlmostEqual(led.total(), 0.5)
        led.actual("k1", 0.1, **self.kw())
        self.assertAlmostEqual(led.total(), 0.1)
        rows = [json.loads(l) for l in self.path.read_text().splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["reserve", "actual"])
        self.assertEqual(set(rows[0]), {"call_key", "usd", "kind", "episode", "actor", "turn", "model", "utc_ns"})

    def test_refuses_above_cap(self):
        led = Ledger(self.path, cap=1.0)
        self.assertTrue(led.reserve("a", 0.6, **self.kw()))
        self.assertFalse(led.reserve("b", 0.5, **self.kw()))
        self.assertTrue(led.reserve("c", 0.4, **self.kw()))
        self.assertAlmostEqual(led.total(), 1.0)
        self.assertEqual(len(self.path.read_text().splitlines()), 2)

    def test_concurrent_threads_never_exceed_cap(self):
        led = Ledger(self.path, cap=1.0)
        results = []
        lock = threading.Lock()

        def worker(i):
            ok = led.reserve(f"k{i}", 0.07, **self.kw(i))
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 14)  # floor(1.0 / 0.07)
        self.assertLessEqual(led.total(), 1.0 + 1e-9)

    def test_two_ledger_objects_share_the_cap(self):
        a, b = Ledger(self.path, cap=1.0), Ledger(self.path, cap=1.0)
        results = []

        def worker(led, i):
            results.append(led.reserve(f"k{i}", 0.09, **self.kw(i)))

        threads = [threading.Thread(target=worker, args=((a, b)[i % 2], i)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 11)
        self.assertLessEqual(a.total(), 1.0 + 1e-9)


if __name__ == "__main__":
    unittest.main()
