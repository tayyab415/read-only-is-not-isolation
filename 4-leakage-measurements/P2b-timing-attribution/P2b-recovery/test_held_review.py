"""Exercise the descriptive helper against the complete retained native log."""
import json
import unittest

import review_held


class GatewaySelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = review_held.DATA / "pad"
        cls.rows = review_held.read(folder / "holdout-server.json")["gateway_log"]
        trials = [json.loads(line) for line in
                  (folder / "holdout.jsonl").read_text().splitlines()]
        cls.wanted = {row["marker"] for trial in trials
                      for row in trial["post"] + trial["sender_records"]}
        cls.marked = next(row for row in cls.rows
                          if row.get("marker") in cls.wanted)

    def test_actual_ready_record_does_not_hide_primary_requests(self):
        self.assertTrue(any("marker" not in row for row in self.rows))
        selected = review_held.primary_gateway_rows(self.rows, self.wanted)
        self.assertEqual(len(selected), 25500)
        self.assertGreaterEqual(min(row["headers_started_utc_ns"] -
                                    row["start_utc_ns"] for row in selected),
                                12_000_000)

    def test_missing_primary_request_refuses(self):
        rows = [row for row in self.rows if row is not self.marked]
        with self.assertRaisesRegex(RuntimeError, "do not join"):
            review_held.primary_gateway_rows(rows, self.wanted)

    def test_duplicate_primary_request_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "do not join"):
            review_held.primary_gateway_rows(self.rows + [self.marked], self.wanted)


if __name__ == "__main__":
    unittest.main()
