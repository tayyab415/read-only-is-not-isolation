"""Regressions against all actual first-edition smoke records."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "P9-content"))
import native_storage


class Headers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells = {}
        for cell in native_storage.CELLS:
            p = ROOT / "P9-content/archive/science-v1/data" / cell
            cls.cells[cell] = (
                json.loads((p / "smoke-provision.json").read_text()),
                [json.loads(line) for line in (p / "smoke.jsonl").read_text().splitlines()],
                json.loads((p / "smoke-native-before.json").read_text()),
                json.loads((p / "smoke-native-after.json").read_text()))

    def checked(self, cell="equal", trials=None):
        public, original, before, after = self.cells[cell]
        return native_storage.validate_public_evidence(
            public, original if trials is None else trials, before, after)

    def test_actual_worker_header_arrays_validate_all_cells(self):
        for cell in self.cells:
            checks = self.checked(cell)
            self.assertTrue(all(checks.values()))

    def test_exact_package_paths_exclude_the_retained_blocked_stats(self):
        for cell in self.cells:
            checks = self.checked(cell)
            self.assertEqual(sum(k.startswith("receiver stable hot object ") for k in checks), 8 * 17)
            rows = self.cells[cell][1]
            self.assertEqual([r["status"] for t in rows for r in t["post"]
                              if r["probe"] == "post.after.stats"], [403] * 8)

    def mutated(self):
        rows = copy.deepcopy(self.cells["equal"][1])
        return rows, rows[0]["post"][0]

    def test_missing_etag_refuses(self):
        rows, row = self.mutated()
        row["headers"] = [(k, v) for k, v in row["headers"] if k.lower() != "etag"]
        self.assertFalse(all(self.checked(trials=rows).values()))

    def test_duplicate_etag_refuses(self):
        rows, row = self.mutated()
        row["headers"].append(["ETag", self.cells["equal"][0]["fixture_etag"]])
        self.assertFalse(all(self.checked(trials=rows).values()))

    def test_wrong_etag_refuses(self):
        rows, row = self.mutated()
        row["headers"] = [(k, "wrong" if k.lower() == "etag" else v) for k, v in row["headers"]]
        self.assertFalse(all(self.checked(trials=rows).values()))

    def test_wrong_receiver_body_refuses(self):
        rows, row = self.mutated()
        row["body_sha256"] = "0" * 64
        self.assertFalse(all(self.checked(trials=rows).values()))


if __name__ == "__main__":
    unittest.main()
