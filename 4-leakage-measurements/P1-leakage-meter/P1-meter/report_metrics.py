#!/usr/bin/env python3
"""Apply the pre-holdout reporting families without altering analysis v2."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.stats import binom, binomtest

from analysis import RUNS, entropy2, holm

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def power(n, alpha, accuracy):
    critical = next(k for k in range(n + 1) if binom.sf(k - 1, n, 0.5) <= alpha)
    return {"critical_correct": critical, "assumed_accuracy": accuracy,
            "probability_of_rejection": float(binom.sf(critical - 1, n, accuracy))}


def calculate(results):
    classifier_q = holm([results[run]["permutation_p"] for run in RUNS])
    confirmations = [{**row, "run": run} for run in RUNS for row in results[run]["confirmatory"]]
    for row, q in zip(confirmations, holm([row["p"] for row in confirmations])):
        row["p_holm_experiment"] = q
    breakdowns = [{"run": run, "recipe": recipe, **row}
                  for run in RUNS for recipe, row in results[run]["per_recipe"].items()]
    for row, q in zip(breakdowns, holm([row["p_one_sided"] for row in breakdowns])):
        row["p_holm_experiment"] = q
    classifiers = {}
    for run, q in zip(RUNS, classifier_q):
        row = results[run]
        interval = binomtest(row["correct"], row["trials"]).proportion_ci(confidence_level=0.99)
        lower = float(interval.low)
        classifiers[run] = {"accuracy": row["accuracy"], "trials": row["trials"],
                            "permutation_p": row["permutation_p"], "p_holm_experiment": q,
                            "accuracy_interval_99_binomial": [lower, float(interval.high)],
                            "information_plugin_bits_per_trial": row["bits_per_trial_lower_bound"],
                            "information_from_99_interval_lower_bits_per_trial":
                            1 - entropy2(1 - lower) if lower > 0.5 else 0.0,
                            "interval_assumption": "Independent Bernoulli trial outcomes; five intervals use Bonferroni."}
    nulls = {}
    for run in ["aa", "sham"]:
        row = results[run]
        nulls[run] = {"classifier_nominal_pass": row["permutation_p"] >= 0.05,
                     "selected_feature_nominal_pass": not any(
                         item["p_holm_confirmatory"] < 0.05 for item in row["confirmatory"]),
                     "exploratory_scan_nominal_pass": not row["exploratory_holm_significant"]}
        nulls[run]["passed"] = all(nulls[run].values())
    return {"classifiers": classifiers, "confirmations": confirmations, "recipe_decoders": breakdowns,
            "physical_nulls": nulls, "null_gate_passed": all(row["passed"] for row in nulls.values()),
            "pooled_classifier_power_bonferroni_0_01":
            [power(1000, 0.01, value) for value in [0.53, 0.54, 0.55, 0.56, 0.57, 0.60]],
            "scope": "Association and fixed-decoder estimates; causal audit and known-channel validation remain separate gates."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    policy = json.loads((HERE / "REPORTING-FROZEN.json").read_text())
    for name, expected in policy["sha256"].items():
        if sha(HERE / name) != expected:
            raise SystemExit(f"Reporting source changed after freeze: {name}")
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise SystemExit(f"Analysis freeze changed: {name}")
    results = json.loads(args.results.read_text())
    if set(results) != set(RUNS) or any(row["trials"] != 1000 for row in results.values()):
        raise SystemExit("Complete 1,000-trial holdouts for all five runs are required")
    output = calculate(results)
    output["input_sha256"] = sha(args.results)
    output["reporting_freeze_sha256"] = sha(HERE / "REPORTING-FROZEN.json")
    path = args.results.parent / "report-metrics.json"
    path.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"null_gate_passed": output["null_gate_passed"], "classifiers": output["classifiers"]}, indent=2))


if __name__ == "__main__":
    main()
