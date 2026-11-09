"""Exhaustive small-label assignments check the independent randomization math."""
from itertools import combinations, product
import unittest

from verify import exact_tail


class Exact(unittest.TestCase):
    def test_matches_exhaustive_two_cell_distribution(self):
        labels = [0, 1, 0, 1, 1, 0, 0, 1]
        cells = [list(range(4)), list(range(4, 8))]
        assignments = []
        for cell in cells:
            assignments.append(list(combinations(cell, 2)))
        for predictions in ([0] * 8, labels, [1 - y for y in labels],
                            [1, 1, 1, 0, 0, 1, 0, 0]):
            observed = sum(y == p for y, p in zip(labels, predictions))
            exceed = total = 0
            for chosen in product(*assignments):
                positive = {i for group in chosen for i in group}
                shuffled = [int(i in positive) for i in range(8)]
                exceed += sum(y == p for y, p in zip(shuffled, predictions)) >= observed
                total += 1
            self.assertAlmostEqual(exact_tail(labels, predictions, cells), exceed / total, places=12)

    def test_partition_omissions_and_duplicates_fail(self):
        for cells in ([[0]], [[0, 1], [1]]):
            with self.assertRaises(ValueError):
                exact_tail([0, 1], [0, 1], cells)


if __name__ == "__main__":
    unittest.main()
