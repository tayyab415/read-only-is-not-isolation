"""Check frozen P8 arithmetic; retain the original Monte Carlo reporting family."""
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
P8 = HERE.parent / "P8-telemetry"
ARMS = ("native", "masked", "aa-native", "aa-masked")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def probability_equal(left, right):
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-300)


def close(left, right):
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-13)


def block_distribution(labels, predictions, blocks):
    if not labels or not len(labels) == len(predictions) == len(blocks) \
            or any(value not in (0, 1) for value in labels + predictions):
        raise ValueError("Complete binary vectors required")
    groups = [[i for i, block in enumerate(blocks) if block == key]
              for key in sorted(set(blocks))]
    distribution, denominator = Counter({0: 1}), 1
    for group in groups:
        if len(group) != 4 or sum(labels[i] for i in group) != 2:
            raise ValueError("Each original block must have four trials and two active labels")
        predicted = sum(predictions[i] for i in group)
        weights = Counter()
        for true_positive in range(max(0, predicted - 2), min(predicted, 2) + 1):
            correct = 2 - predicted + 2 * true_positive
            weights[correct] = math.comb(predicted, true_positive) \
                * math.comb(4 - predicted, 2 - true_positive)
        combined = Counter()
        for score, count in distribution.items():
            for extra, weight in weights.items():
                combined[score + extra] += count * weight
        distribution, denominator = combined, denominator * 6
    if sum(distribution.values()) != denominator:
        raise RuntimeError("Exact distribution lost assignment mass")
    return distribution, denominator


def exact_tail(labels, predictions, blocks):
    distribution, denominator = block_distribution(labels, predictions, blocks)
    observed = sum(label == predicted for label, predicted in zip(labels, predictions))
    return Fraction(sum(count for score, count in distribution.items() if score >= observed), denominator)


def feature(samples):
    if len(samples) != 22:
        raise ValueError("All twenty-two receiver samples required")
    endpoints = []
    for sample in (samples[0], samples[-1]):
        fields = sample["cpu_line"].split()
        if len(fields) < 9 or fields[0] != "cpu":
            raise ValueError("Aggregate CPU row required")
        values = [int(value) for value in fields[1:]]
        if any(value < 0 for value in values):
            raise ValueError("Negative counter")
        total = sum(values[:8])
        endpoints.append((total, total - values[3] - values[4]))
    total = endpoints[1][0] - endpoints[0][0]
    busy = endpoints[1][1] - endpoints[0][1]
    if total < 0 or busy < 0 or busy > total:
        raise ValueError("Invalid accounting delta")
    return busy / total if total else 0.0


def prediction(model, value):
    if model["kind"] == "constant" and model["value"] in (0, 1):
        return model["value"]
    if model["kind"] != "threshold" or model["direction"] not in (-1, 1):
        raise ValueError("Unknown frozen decoder")
    return int(value >= model["threshold"]) if model["direction"] == 1 \
        else int(value < model["threshold"])


def holm(values):
    corrected, previous = {}, 0.0
    for index, (name, value) in enumerate(sorted(values.items(), key=lambda pair: pair[1])):
        previous = max(previous, min(1.0, value * (len(values) - index)))
        corrected[name] = previous
    return corrected


def interval(correct, count):
    # Invert binomial tails instead of the primary beta quantile implementation.
    from scipy.optimize import brentq
    from scipy.stats import binom
    lower = brentq(lambda p: binom.sf(correct - 1, count, p) - .005, 0, 1, xtol=1e-14) \
        if correct else 0.0
    upper = brentq(lambda p: binom.cdf(correct, count, p) - .005, 0, 1, xtol=1e-14) \
        if correct < count else 1.0
    return [lower, upper]


def verify():
    policy = json.loads((HERE / "VERIFICATION-FROZEN.json").read_text())
    for name, expected in policy["sha256"].items():
        require(sha(HERE / name) == expected, "Verification source changed")
    require(sha(P8 / "SOURCES-FROZEN.json") == policy["p8_source_freeze_sha256"],
            "P8 scientific edition changed")
    sys.path.insert(0, str(P8))
    import audit as native_audit
    import analysis as primary
    import collector
    import runtime
    runtime.check_sources()
    frozen = json.loads((P8 / "FROZEN.json").read_text())
    data = P8 / frozen["data"]
    collector.verify_freeze(data)
    # Every complete seal is required before a single held observation is parsed.
    for arm in ARMS:
        folder = data / arm
        seal = json.loads((folder / "holdout-sealed.json").read_text())
        require(seal["complete"] is True and seal["cleanup_absence_proved"] is True
                and seal["trials"] == seal["retained_trials"] == 64, "Incomplete P8 holdout")
        files = {**seal["evidence_sha256"], "holdout.jsonl": seal["sha256"]}
        for name, expected in files.items():
            require(sha(folder / name) == expected, "Held input differs from its seal")
    retained = primary.checked_audit(data, "holdout")
    replay = native_audit.audit_all(data, "holdout")
    require(replay["passed"] is True and replay == retained,
            "Read-only native replay differs from retained audit")
    model_path = P8 / frozen["models"]
    models = json.loads(model_path.read_text())
    result_path = model_path.parent / "holdout-results.json"
    claimed = json.loads(result_path.read_text())
    require(claimed["model_freeze_sha256"] == sha(P8 / "FROZEN.json")
            and claimed["audit_sha256"] == sha(data / "holdout-audit-results.json")
            and claimed["audit_passed"] is True and claimed["permutations"] == 9999
            and claimed["family"] == list(ARMS) and claimed["primary_tests"] == 4
            and claimed["correction"] == "Holm", "Primary bindings differ")
    checks, exact_values, primary_values = {}, {}, {}
    for arm in ARMS:
        rows = [json.loads(line) for line in (data / arm / "holdout.jsonl").read_text().splitlines()]
        require(len(rows) == 64 and all(row["controller_start_utc_ns"] > frozen["utc_ns"]
                                       for row in rows), "Held collection preceded decoder freeze")
        values = [feature(row["receiver"]["samples"]) for row in rows]
        model = models["models"][arm]
        predictions = [prediction(model, value) for value in values]
        labels, blocks = [row["label"] for row in rows], [row["block"] for row in rows]
        counts = Counter(zip(labels, predictions))
        confusion = {"tp": counts[1, 1], "fp": counts[0, 1],
                     "fn": counts[1, 0], "tn": counts[0, 0]}
        correct = confusion["tp"] + confusion["tn"]
        report = claimed["results"][arm]
        exact = float(exact_tail(labels, predictions, blocks))
        exact_values[arm] = exact
        primary_values[arm] = report["uncorrected_blocked_pvalue"]
        require(1 / 10000 <= primary_values[arm] <= 1
                and close(primary_values[arm] * 10000, round(primary_values[arm] * 10000)),
                "Primary probability is outside its plus-one Monte Carlo grid")
        tolerance = 5 * math.sqrt(exact * (1 - exact) / 9999) + 2 / 10000
        by_label = {str(label): {
            "busy_fraction_median": statistics.median(v for v, y in zip(values, labels) if y == label),
            "sender_process_cpu_seconds_median": statistics.median(
                row["sender"]["load"]["process_cpu_ns"] / 1e9 for row in rows if row["label"] == label)}
                    for label in (0, 1)}
        durations = [(row["controller_end_utc_ns"] - row["controller_start_utc_ns"]) / 1e9 for row in rows]
        numeric = report["confusion"] == confusion and report["correct"] == correct \
            and report["trials"] == 64 and report["model"] == model \
            and report["predictions"] == predictions and report["labels"] == labels \
            and report["counter_feature_values"] == values and report["by_label"] == by_label \
            and close(report["accuracy"], correct / 64) \
            and close(report["true_positive_rate"], confusion["tp"] / 32) \
            and close(report["false_positive_rate"], confusion["fp"] / 32) \
            and all(close(a, b) for a, b in zip(report["accuracy_99pct_iid_binomial_interval"],
                                               interval(correct, 64))) \
            and len(report["accuracy_99pct_iid_binomial_interval"]) == 2 \
            and close(report["trial_wall_seconds_median"], statistics.median(durations)) \
            and close(report["trial_wall_seconds_total"], sum(durations))
        checks[arm] = {"counts_and_features_match": numeric,
                      "exact_conditional_randomization_p": exact,
                      "primary_monte_carlo_p": primary_values[arm],
                      "monte_carlo_comparison_tolerance": tolerance,
                      "monte_carlo_consistent": abs(primary_values[arm] - exact) <= tolerance,
                      "confusion": confusion, "correct": correct, "trials": 64}
    corrected, exact_corrected = holm(primary_values), holm(exact_values)
    correction_matches = all(probability_equal(claimed["results"][arm]["holm_pvalue"], corrected[arm])
                             for arm in ARMS)
    primary_null = all(corrected[arm] >= .05 for arm in ("aa-native", "aa-masked"))
    exact_null = all(exact_corrected[arm] >= .05 for arm in ("aa-native", "aa-masked"))
    result = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "passed": correction_matches and claimed["null_gate_passed"] is primary_null
                  and all(row["counts_and_features_match"] and row["monte_carlo_consistent"]
                          for row in checks.values()),
        "native_audit_exact_replay": True, "native_audit_checks": replay["checks"],
        "numerical_checks": checks, "primary_holm": corrected, "exact_sensitivity_holm": exact_corrected,
        "primary_aa_null_passed": primary_null, "exact_sensitivity_aa_null_passed": exact_null,
        "holdout_results_sha256": sha(result_path), "model_freeze_sha256": sha(P8 / "FROZEN.json"),
        "verification_freeze_sha256": sha(HERE / "VERIFICATION-FROZEN.json"),
        "scope": "Independent counter-feature/prediction/count arithmetic and exact block randomization; reuses the original native auditor. Exact sensitivity does not replace the primary Monte Carlo/Holm family. No general isolation or P2-cause claim.",
    }
    with (HERE / "RESULT.json").open("x") as stream:
        json.dump(result, stream, indent=2)
    require(result["passed"], "Retained numerical verification failed")
    return result


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
