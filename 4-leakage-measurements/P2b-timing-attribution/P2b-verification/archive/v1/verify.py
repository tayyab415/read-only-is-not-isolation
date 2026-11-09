"""Independent P2b arithmetic, fixed before its data; never replace primary tests."""
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent / "P2b-attribution"
ARMS = ("shared", "pergw", "pad", "aa")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def binomial_tail(correct, trials):
    return Fraction(sum(math.comb(trials, value) for value in range(correct, trials + 1)), 2 ** trials)


def fisher_two_sided(a, b, n):
    successes = a + b
    observed = math.comb(n, a) * math.comb(n, b)
    weights = [math.comb(n, x) * math.comb(n, successes - x)
               for x in range(max(0, successes - n), min(n, successes) + 1)]
    return Fraction(sum(weight for weight in weights if weight <= observed),
                    math.comb(2 * n, successes))


def holm(values):
    adjusted = [Fraction(1)] * len(values)
    running = Fraction(0)
    for rank, index in enumerate(sorted(range(len(values)), key=lambda i: values[i])):
        running = max(running, min(Fraction(1), (len(values) - rank) * values[index]))
        adjusted[index] = running
    return adjusted


def conditional_tail(rows):
    """Exact label randomization given each sender's fixed balance and predictions."""
    strata = defaultdict(list)
    for label, prediction, sender in rows:
        strata[sender].append((label, prediction))
    distribution, denominator = {0: 1}, 1
    observed = sum(label == prediction for label, prediction, _ in rows)
    for group in strata.values():
        n = len(group)
        positives = sum(label for label, _ in group)
        predicted = sum(prediction for _, prediction in group)
        support = {}
        for overlap in range(max(0, positives - (n - predicted)), min(positives, predicted) + 1):
            correct = n - positives - predicted + 2 * overlap
            support[correct] = math.comb(predicted, overlap) * math.comb(n - predicted, positives - overlap)
        convolved = defaultdict(int)
        for left, left_weight in distribution.items():
            for right, right_weight in support.items():
                convolved[left + right] += left_weight * right_weight
        distribution = dict(convolved)
        denominator *= math.comb(n, positives)
    require(sum(distribution.values()) == denominator, "Exact null weights do not sum to one")
    return Fraction(sum(weight for correct, weight in distribution.items() if correct >= observed),
                    denominator)


def wilson(correct, n):
    z, proportion = 1.959963984540054, correct / n
    scale = 1 + z * z / n
    center = (proportion + z * z / (2 * n)) / scale
    radius = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n)) / scale
    return [center - radius, center + radius]


def score(trials, decoder):
    require(len(trials) == 300, "All 300 held trials are required")
    rows, values, probes = [], [], []
    confusion = Counter()
    for trial in trials:
        require(type(trial["label"]) is int and trial["label"] in (0, 1)
                and trial["sender"] in ("alice", "bob")
                and not trial["failed_request_markers"], "Invalid assignment or retained failed request")
        timed = [row["elapsed_ms"] for row in trial["post"]
                 if str(row.get("probe", "")).startswith("post.timed.")]
        require(len(timed) == 15 and all(type(value) in (int, float)
                and math.isfinite(value) and value >= 0 for value in timed),
                "Exactly fifteen valid timed probes are required")
        value = sorted(timed)[7]
        prediction = int(value < decoder["threshold_ms"]) if decoder["active_is_below"] \
            else int(value > decoder["threshold_ms"])
        rows.append((trial["label"], prediction, trial["sender"]))
        values.append((trial["label"], value))
        probes.extend(timed)
        confusion[(trial["label"], prediction)] += 1
    require(Counter((label, sender) for label, _, sender in rows)
            == Counter({(label, sender): 75 for label in (0, 1) for sender in ("alice", "bob")}),
            "The original sender-direction balance must remain intact")
    correct = confusion[(0, 0)] + confusion[(1, 1)]
    probes.sort()
    return {
        "trials": 300, "correct": correct, "accuracy": correct / 300,
        "accuracy_wilson_95": wilson(correct, 300),
        "tp": confusion[(1, 1)], "tn": confusion[(0, 0)],
        "fp": confusion[(0, 1)], "fn": confusion[(1, 0)],
        "tpr": confusion[(1, 1)] / 150, "fpr": confusion[(0, 1)] / 150,
        "median_S_active_ms": statistics.median(value for label, value in values if label == 1),
        "median_S_idle_ms": statistics.median(value for label, value in values if label == 0),
        # Preserve the original reported order statistics for numerical comparison.
        "probe_latency_median_ms": probes[len(probes) // 2],
        "probe_latency_p95_ms": probes[int(.95 * (len(probes) - 1))],
        "failed_trials": 0,
        "primary_binomial_p": binomial_tail(correct, 300),
        "conditional_sensitivity_p": conditional_tail(rows),
    }


def close(actual, expected):
    return math.isclose(float(actual), float(expected), rel_tol=1e-10, abs_tol=1e-13)


def main():
    policy = read(HERE / "VERIFICATION-FROZEN.json")
    require(all(sha(HERE / name) == expected for name, expected in policy["sha256"].items()),
            "Prospective verification source changed")
    require(sha(STUDY / "SOURCES-SHA256.txt") == policy["p2b_source_manifest_sha256"],
            "P2b scientific edition changed")
    sys.path.insert(0, str(STUDY))
    import analyze
    import audit
    import p2b
    p2b.check_sources()
    data = STUDY / "data"
    # Check every seal/hash first, before any held observation is parsed.
    frozen = analyze.verify_freeze(data)
    analyze.check_split_audit(data, "holdout")
    native = read(data / "audit-holdout.json")
    replayed = [audit.audit_arm(data / arm, arm, "holdout") for arm in ARMS]
    require(replayed == native["runs"] and all(row["passed"] is True for row in replayed),
            "Read-only native audit differs")
    primary = read(STUDY / "results.json")
    require(primary["freeze_sha256"] == sha(STUDY / "DECODERS-FROZEN.json")
            and primary["audit_sha256"] == sha(data / "audit-holdout.json")
            and set(primary["arms"]) == set(ARMS), "Primary result bindings differ")
    scores = {}
    for arm in ARMS:
        with (data / arm / "holdout.jsonl").open() as stream:
            trials = [json.loads(line) for line in stream]
        decoder = frozen["decoders"][arm]
        require(type(decoder["active_is_below"]) is bool
                and type(decoder["threshold_ms"]) in (float, int)
                and math.isfinite(decoder["threshold_ms"]), "Invalid frozen decoder")
        scores[arm] = score(trials, decoder)
    primary_adjusted = holm([scores[arm]["primary_binomial_p"] for arm in ARMS])
    conditional_adjusted = holm([scores[arm]["conditional_sensitivity_p"] for arm in ARMS])
    for arm, primary_q, conditional_q in zip(ARMS, primary_adjusted, conditional_adjusted):
        computed, original = scores[arm], primary["arms"][arm]
        for field in ("trials", "correct", "failed_trials"):
            require(computed[field] == original[field], "Primary count differs")
        for field in ("accuracy", "median_S_active_ms", "median_S_idle_ms",
                      "probe_latency_median_ms", "probe_latency_p95_ms"):
            require(close(computed[field], original[field]), "Primary numerical result differs")
        require(close(computed["primary_binomial_p"], original["p_one_sided"])
                and close(primary_q, original["p_holm"]), "Primary exact test or correction differs")
        computed["primary_binomial_p"] = float(computed["primary_binomial_p"])
        computed["conditional_sensitivity_p"] = float(computed["conditional_sensitivity_p"])
        computed["primary_holm_p"] = float(primary_q)
        computed["conditional_sensitivity_holm_p"] = float(conditional_q)
    comparisons = {}
    for arm in ("pergw", "pad"):
        p = fisher_two_sided(scores["shared"]["correct"], scores[arm]["correct"], 300)
        difference = scores["shared"]["accuracy"] - scores[arm]["accuracy"]
        original = primary["comparisons"][f"shared_vs_{arm}"]
        require(close(p, original["fisher_p_two_sided"])
                and close(difference, original["accuracy_difference"]), "Secondary comparison differs")
        comparisons[f"shared_vs_{arm}"] = {"fisher_p_two_sided": float(p),
                                          "accuracy_difference": difference}
    latency = scores["pad"]["probe_latency_median_ms"] - scores["shared"]["probe_latency_median_ms"]
    require(close(latency, primary["comparisons"]["pad_latency_cost_median_ms"]),
            "Padding cost differs")
    comparisons["pad_latency_cost_median_ms"] = latency
    result = {
        "passed": True, "scores": scores, "comparisons": comparisons,
        "primary_aa_raw_nominal_pass": scores["aa"]["primary_binomial_p"] > .05,
        "primary_aa_holm_pass": scores["aa"]["primary_holm_p"] > .05,
        "conditional_aa_sensitivity_pass": scores["aa"]["conditional_sensitivity_holm_p"] > .05,
        "verification_freeze_sha256": sha(HERE / "VERIFICATION-FROZEN.json"),
        "primary_results_sha256": sha(STUDY / "results.json"),
        "decoder_freeze_sha256": sha(STUDY / "DECODERS-FROZEN.json"),
        "native_audit_sha256": sha(data / "audit-holdout.json"),
        "scope": "Independent arithmetic plus predeclared balanced-label sensitivity; original primary tests remain unchanged.",
    }
    with (HERE / "RESULT.json").open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"passed": True, "primary_aa_holm_pass": result["primary_aa_holm_pass"],
                      "primary_results_sha256": result["primary_results_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
