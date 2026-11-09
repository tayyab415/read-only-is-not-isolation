import unittest

import _path  # noqa: F401
import stats


class StatsTests(unittest.TestCase):
    def test_zero_of_48_one_sided_upper(self):
        self.assertAlmostEqual(stats.upper_one_sided(0, 48), 0.0605, places=4)
        self.assertAlmostEqual(stats.upper_one_sided(0, 48), 1 - 0.05 ** (1 / 48), places=9)

    def test_five_of_twenty_two_sided(self):
        lo, hi = stats.clopper_pearson(5, 20)
        self.assertAlmostEqual(lo, 0.0866, places=4)
        self.assertAlmostEqual(hi, 0.4910, places=4)

    def test_edges(self):
        self.assertEqual(stats.clopper_pearson(0, 10)[0], 0.0)
        self.assertEqual(stats.clopper_pearson(10, 10)[1], 1.0)
        self.assertAlmostEqual(stats.clopper_pearson(10, 10)[0], 0.025 ** 0.1, places=9)

    def test_binomial_tail(self):
        self.assertAlmostEqual(stats.binom_test_greater(1, 16, 1 / 16), 1 - (15 / 16) ** 16, places=12)
        self.assertEqual(stats.binom_test_greater(0, 5, 0.5), 1.0)
        self.assertAlmostEqual(stats.binom_cdf(2, 4, 0.5), 11 / 16, places=12)

    def test_fisher(self):
        self.assertAlmostEqual(stats.fisher_exact(3, 1, 1, 3), 0.4857142857, places=9)  # textbook tea tasting
        self.assertAlmostEqual(stats.fisher_exact(10, 0, 0, 10), 2 / 184756, places=12)
        self.assertAlmostEqual(stats.fisher_exact(5, 5, 5, 5), 1.0, places=12)
        self.assertAlmostEqual(stats.fisher_exact(1, 9, 11, 3), 0.0027594561852200836, places=9)  # R: fisher.test(matrix(c(1,11,9,3),2))


if __name__ == "__main__":
    unittest.main()
