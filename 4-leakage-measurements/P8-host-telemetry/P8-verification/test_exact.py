from collections import Counter
from fractions import Fraction
import itertools
import math
import unittest

import verify


class ArithmeticTests(unittest.TestCase):
    def test_all_two_block_prediction_patterns_against_36_assignments(self):
        assignments = []
        for positions in itertools.combinations(range(4), 2):
            assignments.append([int(i in positions) for i in range(4)])
        labels, blocks = [0, 0, 1, 1] * 2, [0] * 4 + [1] * 4
        for predictions in itertools.product((0, 1), repeat=8):
            predictions = list(predictions)
            brute = Counter(sum(a == b for a, b in zip(first + second, predictions))
                            for first in assignments for second in assignments)
            weights, denominator = verify.block_distribution(labels, predictions, blocks)
            self.assertEqual(weights, brute)
            self.assertEqual(denominator, 36)
            observed = sum(a == b for a, b in zip(labels, predictions))
            self.assertEqual(verify.exact_tail(labels, predictions, blocks),
                             Fraction(sum(n for score, n in brute.items() if score >= observed), 36))

    def test_perfect_and_constant_64_trial_limits(self):
        labels, blocks = [0, 0, 1, 1] * 16, [i // 4 for i in range(64)]
        self.assertEqual(verify.exact_tail(labels, labels, blocks), Fraction(1, 6 ** 16))
        self.assertEqual(verify.exact_tail(labels, [0] * 64, blocks), 1)
        self.assertEqual(verify.exact_tail(labels, [1] * 64, blocks), 1)

    def test_broken_assignment_refused(self):
        for labels, predictions, blocks in (
                ([], [], []), ([0, 1], [0], [0, 0]),
                ([0, 0, 0, 1], [0] * 4, [0] * 4),
                ([0, 0, 1, 1], [0] * 4, [0, 0, 0, 1]),
                ([0, 0, 1, 2], [0] * 4, [0] * 4)):
            with self.assertRaises(ValueError):
                verify.exact_tail(labels, predictions, blocks)

    def test_counter_delta_guest_exclusion_and_static_view(self):
        first = "cpu 10 20 30 100 40 5 6 7 99999 88888"
        last = "cpu 20 30 40 150 38 10 16 17 999999 888888"
        samples = [{"cpu_line": first}] * 21 + [{"cpu_line": last}]
        self.assertEqual(verify.feature(samples), 55 / 103)
        self.assertEqual(verify.feature([{"cpu_line": first}] * 22), 0)
        for line in ("cpu -1 0 0 1 0 0 0 0", "cpu0 0 0 0 1 0 0 0 0",
                     "cpu 0 0 0 0 0 0 0 0"):
            with self.assertRaises(ValueError):
                verify.feature([{"cpu_line": first}] * 21 + [{"cpu_line": line}])
        with self.assertRaises(ValueError):
            verify.feature(samples[:-1])

    def test_threshold_equality_and_constants(self):
        for direction, expected in ((1, 1), (-1, 0)):
            self.assertEqual(verify.prediction(
                {"kind": "threshold", "threshold": .5, "direction": direction}, .5), expected)
        for value in (0, 1):
            self.assertEqual(verify.prediction({"kind": "constant", "value": value}, .8), value)

    def test_binomial_tail_inversion_matches_beta_reference(self):
        from scipy.stats import beta
        for correct in range(65):
            expected = [beta.ppf(.005, correct, 65 - correct) if correct else 0,
                        beta.ppf(.995, correct + 1, 64 - correct) if correct < 64 else 1]
            self.assertTrue(all(math.isclose(a, b, abs_tol=1e-12, rel_tol=1e-10)
                                for a, b in zip(verify.interval(correct, 64), expected)))

    def test_holm_and_tiny_probability_comparison(self):
        self.assertEqual(verify.holm({"a": .01, "b": .04, "c": .03, "d": .9}),
                         {"a": .04, "c": .09, "b": .09, "d": .9})
        self.assertFalse(verify.probability_equal(1e-90, 0))
        self.assertTrue(verify.probability_equal(1e-90, 1e-90 * (1 + 1e-11)))


if __name__ == "__main__":
    unittest.main()
