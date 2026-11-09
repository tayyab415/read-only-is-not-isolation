from fractions import Fraction
from itertools import combinations, product
import math
import unittest

from scipy.stats import fisher_exact
import verify


class ExactTests(unittest.TestCase):
    def test_binomial_tail_matches_exhaustive_coins(self):
        outcomes = [sum(bits) for bits in product((0, 1), repeat=6)]
        for correct in range(7):
            self.assertEqual(verify.binomial_tail(correct, 6),
                             Fraction(sum(value >= correct for value in outcomes), 64))

    def test_conditional_tail_matches_every_balanced_assignment(self):
        assignments = []
        for positions in combinations(range(4), 2):
            assignments.append([int(index in positions) for index in range(4)])
        predictions = ([1, 1, 0, 0], [1, 0, 1, 0])
        scores = [sum(label == prediction for label, prediction in
                      zip(left + right, predictions[0] + predictions[1]))
                  for left, right in product(assignments, repeat=2)]
        for left, right in product(assignments, repeat=2):
            rows = [(label, prediction, sender)
                    for labels, pred, sender in ((left, predictions[0], "alice"),
                                                 (right, predictions[1], "bob"))
                    for label, prediction in zip(labels, pred)]
            correct = sum(label == prediction for label, prediction, _ in rows)
            self.assertEqual(verify.conditional_tail(rows),
                             Fraction(sum(value >= correct for value in scores), 36))

    def test_direction_constant_prediction_has_no_randomization_evidence(self):
        rows = [(label, int(sender == "alice"), sender)
                for sender in ("alice", "bob") for label in (0, 0, 1, 1)]
        self.assertEqual(verify.conditional_tail(rows), 1)

    def test_fisher_exact_matches_reference_in_small_and_extreme_tables(self):
        tables = [(a, b, 6) for a, b in product(range(7), repeat=2)]
        tables += [(a, b, 300) for a, b in ((300, 0), (299, 1), (180, 160), (200, 190), (150, 150))]
        for a, b, n in tables:
            actual = float(verify.fisher_two_sided(a, b, n))
            expected = fisher_exact([[a, n - a], [b, n - b]]).pvalue
            self.assertTrue(math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-300),
                            (a, b, n, actual, expected))

    def test_tiny_p_values_cannot_silently_round_to_zero(self):
        self.assertFalse(verify.close_probability(1e-90, 0))
        self.assertFalse(verify.close_probability(1e-90, 1e-30))
        self.assertTrue(verify.close_probability(1e-90, 1e-90 * (1 + 1e-12)))
        self.assertTrue(verify.close_probability(0, 0))

    def test_holm_known_family(self):
        values = [Fraction(2, 1000), Fraction(8, 1000), Fraction(20, 1000), Fraction(300, 1000)]
        self.assertEqual(verify.holm(values),
                         [Fraction(8, 1000), Fraction(24, 1000), Fraction(40, 1000), Fraction(300, 1000)])

    def trials(self):
        return [{"label": label, "sender": sender, "failed_request_markers": [],
                 "post": [{"probe": f"post.timed.{index}", "elapsed_ms": 1 if label else 3}
                          for index in range(15)]}
                for sender in ("alice", "bob") for label in (0, 1) for _ in range(75)]

    def test_full_scores_and_strict_threshold_ties(self):
        decoder = {"threshold_ms": 2, "active_is_below": True}
        result = verify.score(self.trials(), decoder)
        self.assertEqual((result["correct"], result["tp"], result["tn"], result["fp"], result["fn"]),
                         (300, 150, 150, 0, 0))
        self.assertEqual(result["primary_binomial_p"], Fraction(1, 2 ** 300))
        self.assertEqual(result["conditional_sensitivity_p"], Fraction(1, math.comb(150, 75) ** 2))
        tied = self.trials()
        for trial in tied:
            for row in trial["post"]:
                row["elapsed_ms"] = 2
        result = verify.score(tied, decoder)
        self.assertEqual((result["correct"], result["tp"], result["fp"], result["fn"]), (150, 0, 0, 150))
        self.assertEqual(result["conditional_sensitivity_p"], 1)

    def test_incomplete_failed_or_unbalanced_observations_are_rejected(self):
        decoder = {"threshold_ms": 2, "active_is_below": True}
        for kind in ("missing_trial", "failed", "probe_count", "nonfinite", "balance"):
            trials = self.trials()
            if kind == "missing_trial":
                trials.pop()
            elif kind == "failed":
                trials[0]["failed_request_markers"] = ["retained-failure"]
            elif kind == "probe_count":
                trials[0]["post"].pop()
            elif kind == "nonfinite":
                trials[0]["post"][0]["elapsed_ms"] = float("nan")
            else:
                trials[0]["label"] = 1
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                verify.score(trials, decoder)


if __name__ == "__main__":
    unittest.main()
