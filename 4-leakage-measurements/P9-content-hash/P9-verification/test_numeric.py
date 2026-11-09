"""Check the pinned integer arithmetic against enumerated small assignments."""
from fractions import Fraction
import itertools
import unittest
import verify

arithmetic = verify.module("p9_test_integer_reference", verify.REFERENCE)


class Numerical(unittest.TestCase):
    def test_conditional_tail_matches_direct_balanced_enumeration(self):
        assignments = list(itertools.combinations(range(4), 2))
        predictions = (0, 1, 1, 0, 1, 1, 0, 0)
        for left, right in itertools.product(assignments, repeat=2):
            labels = tuple(int(i in left) for i in range(4)) + tuple(int(i in right) for i in range(4))
            observed = sum(a == b for a, b in zip(labels, predictions))
            tail = 0
            for a, b in itertools.product(assignments, repeat=2):
                draw = tuple(int(i in a) for i in range(4)) + tuple(int(i in b) for i in range(4))
                tail += sum(x == y for x, y in zip(draw, predictions)) >= observed
            self.assertEqual(arithmetic.conditional_tail([
                (label, prediction, "alice" if i < 4 else "bob")
                for i, (label, prediction) in enumerate(zip(labels, predictions))]),
                Fraction(tail, 36))

    def test_holm_known_family(self):
        self.assertEqual(arithmetic.holm([Fraction(1, 100), Fraction(1, 5), Fraction(1), Fraction(1, 2)]),
                         [Fraction(1, 25), Fraction(3, 5), Fraction(1), Fraction(1)])

    def test_fisher_extremes_and_constant_scores(self):
        self.assertEqual(arithmetic.fisher_two_sided(0, 4, 4), Fraction(1, 35))
        self.assertEqual(arithmetic.fisher_two_sided(2, 2, 4), Fraction(1))

    def test_verifier_load_has_no_native_or_result_side_effect(self):
        self.assertFalse((verify.HERE / "RESULT.json").exists())
        self.assertFalse((verify.STUDY / "data").exists())


if __name__ == "__main__":
    unittest.main()
