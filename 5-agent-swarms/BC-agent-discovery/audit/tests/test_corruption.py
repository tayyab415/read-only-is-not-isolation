import unittest

import _path  # noqa: F401
import corruption_tests as ct


class Corruptions(unittest.TestCase):
    def test_each_corruption_reports_its_code(self):
        for name in ct.CASES:
            with self.subTest(case=name):
                r = ct.run_case(name)
                self.assertTrue(r["pass"], r)


if __name__ == "__main__":
    unittest.main()
