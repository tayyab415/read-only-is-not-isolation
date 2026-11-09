import json
import time
import unittest

import experiment as e
import participant
import runtime


def samples(first, last):
    return [{"cpu_line": first}, {"cpu_line": last}]


class ExperimentTests(unittest.TestCase):
    def test_all_fixed_denominators_and_balanced_blocks(self):
        for configuration in e.CONFIGURATIONS:
            for split, count in (("smoke", 4), ("calibration", 24), ("holdout", 64)):
                rows = e.schedule(configuration, split)
                self.assertEqual(len(rows), count)
                self.assertEqual(rows, e.schedule(configuration, split))
                for index in range(0, count, 4):
                    self.assertEqual(sorted(row["label"] for row in rows[index:index + 4]), [0, 0, 1, 1])

    def test_no_denominator_override(self):
        with self.assertRaises(ValueError):
            e.schedule("native", "holdout", 1000)

    def test_fresh_opaque_markers_across_cells(self):
        tokens = [row["token"] for arm in e.CONFIGURATIONS for split in ("smoke", "calibration", "holdout")
                  for row in e.schedule(arm, split)]
        self.assertEqual(len(tokens), len(set(tokens)))

    def test_physically_idle_aa_labels(self):
        for arm in ("aa-native", "aa-masked"):
            self.assertEqual([e.executing(arm, label) for label in (0, 1)], [0, 0])
        self.assertEqual([e.executing("native", label) for label in (0, 1)], [0, 1])

    def test_excludes_double_counted_guest(self):
        result = e.cpu_feature(samples("cpu 100 0 0 100 0 0 0 0 900 0",
                                       "cpu 150 0 0 150 0 0 0 0 1900 0"))
        self.assertEqual(result["value"], .5)
        self.assertEqual(result["total_delta"], 100)

    def test_preserves_decreasing_iowait(self):
        result = e.cpu_feature(samples("cpu 100 0 0 100 10 0 0 0 0 0",
                                       "cpu 110 0 0 120 9 0 0 0 0 0"))
        self.assertEqual(result["iowait_delta"], -1)
        self.assertAlmostEqual(result["value"], 10 / 29)

    def test_constant_mask_is_unavailable_live_input(self):
        line = e.static_view(4).splitlines()[0].decode()
        result = e.cpu_feature(samples(line, line))
        self.assertEqual(result["value"], 0)
        self.assertFalse(result["live_counter"])

    def test_invalid_decreasing_total_fails(self):
        with self.assertRaises(ValueError):
            e.cpu_feature(samples("cpu 100 0 0 100 0 0 0 0", "cpu 90 0 0 90 0 0 0 0"))

    def test_fit_and_frozen_prediction_both_directions(self):
        for inverted in (False, True):
            records = [{"label": label, "receiver": {"samples": samples(
                "cpu 0 0 0 0 0 0 0 0", "cpu " + ("9 0 0 1" if bool(label) != inverted else "1 0 0 9") +
                " 0 0 0 0")}} for label in (0, 0, 1, 1)]
            model = e.fit(records)
            self.assertEqual(model["calibration_correct"], 4)
            self.assertEqual([e.predict(model, e.cpu_feature(row["receiver"]["samples"])["value"])
                              for row in records], [0, 0, 1, 1])

    def test_constant_fit_tie_is_predeclared_idle(self):
        line = e.static_view(4).splitlines()[0].decode()
        rows = [{"label": label, "receiver": {"samples": samples(line, line)}} for label in (0, 0, 1, 1)]
        self.assertEqual(e.fit(rows)["value"], 0)

    def test_blocked_null_constant_prediction(self):
        self.assertEqual(e.blocked_pvalue([0] * 8, [0, 0, 1, 1] * 2, [0] * 4 + [1] * 4, 99), 1)

    def test_blocked_perfect_prediction_has_small_p(self):
        labels = [0, 0, 1, 1] * 8
        self.assertLess(e.blocked_pvalue(labels, labels, [i // 4 for i in range(32)], 999), .01)

    def test_blocked_balance_violation_rejected(self):
        with self.assertRaises(ValueError):
            e.blocked_pvalue([0] * 4, [0] * 4, [0] * 4, 9)

    def test_holm_is_full_family_monotonic(self):
        values = e.holm({"a": .001, "b": .02, "c": .3, "d": 1})
        self.assertEqual(values, {"a": .004, "b": .06, "c": .6, "d": 1})

    def test_receiver_rejects_ground_truth(self):
        message = {"op": "trial", "epoch_utc_ns": time.time_ns() + 100_000_000, "token": "a" * 16}
        participant.validate_message(message, "bob")
        with self.assertRaises(ValueError):
            participant.validate_message({**message, "execute": 1}, "bob")
        with self.assertRaises(ValueError):
            participant.validate_message({**message, "label": 0}, "bob")

    def test_sender_execution_flag_strict(self):
        message = {"op": "trial", "epoch_utc_ns": time.time_ns() + 100_000_000, "token": "a" * 16}
        for invalid in (True, "1", 2, -1):
            with self.assertRaises(ValueError):
                participant.validate_message({**message, "execute": invalid}, "alice")

    def test_guest_has_no_assignment_generator(self):
        source = runtime.guest_program()
        self.assertNotIn("'experiment'", source)
        self.assertNotIn("schedule(configuration", source)
        self.assertIn("'observer_contract'", source)


if __name__ == "__main__":
    unittest.main()
