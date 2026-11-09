"""Reporting corrections must not turn a nominally failing null into a pass."""
import copy
import math
import unittest

from scipy.stats import binom

import report_metrics


def synthetic():
    return {run: {"correct": 500, "trials": 1000, "accuracy": 0.5,
                  "permutation_p": 1.0, "bits_per_trial_lower_bound": 0.0,
                  "confirmatory": [], "per_recipe": {}, "exploratory_holm_significant": []}
            for run in report_metrics.RUNS}


class ReportingTests(unittest.TestCase):
    def test_confirmation_is_corrected_across_configurations(self):
        data = synthetic()
        for index, run in enumerate(report_metrics.RUNS):
            data[run]["confirmatory"] = [{"feature": "response", "recipe": None,
                                          "p": 0.02 if index == 0 else 1.0,
                                          "p_holm_confirmatory": 0.02 if index == 0 else 1.0}]
        result = report_metrics.calculate(data)
        self.assertAlmostEqual(result["confirmations"][0]["p_holm_experiment"], 0.1)

    def test_global_correction_does_not_excuse_nominal_null_failure(self):
        data = synthetic()
        data["aa"]["permutation_p"] = 0.02
        result = report_metrics.calculate(data)
        self.assertAlmostEqual(result["classifiers"]["aa"]["p_holm_experiment"], 0.1)
        self.assertFalse(result["null_gate_passed"])

    def test_plugin_information_is_not_confused_with_confidence_lower_value(self):
        data = synthetic()
        data["open"].update(correct=520, accuracy=0.52, bits_per_trial_lower_bound=0.001)
        result = report_metrics.calculate(data)["classifiers"]["open"]
        self.assertEqual(result["information_plugin_bits_per_trial"], 0.001)
        self.assertEqual(result["information_from_99_interval_lower_bits_per_trial"], 0.0)

    def test_power_uses_the_actual_critical_count(self):
        low = report_metrics.power(1000, 0.01, 0.54)
        high = report_metrics.power(1000, 0.01, 0.57)
        k = low["critical_correct"]
        self.assertLessEqual(binom.sf(k - 1, 1000, 0.5), 0.01)
        self.assertGreater(binom.sf(k - 2, 1000, 0.5), 0.01)
        self.assertLess(low["probability_of_rejection"], high["probability_of_rejection"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
