"""Read-only numerical cross-check of P2's frozen predictions.

Reuses the frozen feature extraction and models. Recomputes confusion counts
and the exact stratified label-randomization distribution independently of the
primary Monte Carlo test. This is a check, not a replacement reporting rule.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
from scipy.stats import hypergeom

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
sys.path.insert(0, str(P2))
import analysis as frozen_analysis
import audit as frozen_audit
import collect as frozen_collect


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_tail(labels, predictions, cells):
    """Condition on each cell's label and prediction totals."""
    labels, predictions = list(labels), list(predictions)
    if len(labels) != len(predictions) or any(v not in (0, 1) for v in labels + predictions):
        raise ValueError("Binary vectors must have equal lengths")
    indices = [i for cell in cells for i in cell]
    if sorted(indices) != list(range(len(labels))):
        raise ValueError("Cells must partition all observations exactly once")
    distribution = np.array([1.0])
    for cell in cells:
        n = len(cell)
        if not n:
            raise ValueError("Empty cell")
        positives = sum(labels[i] for i in cell)
        predicted = sum(predictions[i] for i in cell)
        mass = np.zeros(n + 1)
        for both in range(max(0, predicted + positives - n), min(predicted, positives) + 1):
            correct = n - positives - predicted + 2 * both
            mass[correct] = hypergeom.pmf(both, n, positives, predicted)
        distribution = np.convolve(distribution, mass)
    if abs(distribution.sum() - 1) > 1e-10:
        raise RuntimeError("Randomization distribution lost probability mass")
    observed = sum(a == b for a, b in zip(labels, predictions))
    return float(distribution[observed:].sum())


def verify():
    policy = json.loads((HERE / "VERIFICATION-FROZEN.json").read_text())
    for name, expected in policy["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError("Verification code changed")
    if sha(P2 / "FROZEN.json") != policy["p2_model_freeze_sha256"]:
        raise RuntimeError("P2 model freeze changed")
    frozen_analysis.validate_sources()
    freeze = json.loads((P2 / "FROZEN.json").read_text())
    data, output = P2 / freeze["data"], P2 / freeze["out"]
    frozen_collect.verify_model_freeze(data)
    for arm in frozen_analysis.plan.CONFIGURATIONS:
        folder = data / arm
        seal = json.loads((folder / "holdout-sealed.json").read_text())
        if not seal["complete"] or seal["trials"] != 1000 or not seal["cleanup_absence_proved"]:
            raise RuntimeError("All complete, cleaned-up holdouts are required")
        hashes = {**seal["evidence_sha256"], "holdout.jsonl": seal["sha256"],
                  "holdout-provision.json": seal["provision_sha256"]}
        if any(sha(folder / name) != expected for name, expected in hashes.items()):
            raise RuntimeError("Held input does not match its seal")
    claimed = json.loads((output / "holdout-results.json").read_text())
    frozen_analysis.check_audit(data, "holdout")
    native = [frozen_audit.audit_split(data / arm, arm, "holdout")
              for arm in frozen_analysis.plan.CONFIGURATIONS]
    checks = {}
    for arm in frozen_analysis.plan.CONFIGURATIONS:
        trials, values, labels, recipes, senders = frozen_analysis.prepare(data, arm, "holdout")
        with (output / f"{arm}.model.pkl").open("rb") as stream:
            fit = pickle.load(stream)
        predictions = fit["model"].predict(fit["encoder"].transform(values))
        confusion = Counter((int(y), int(p)) for y, p in zip(labels, predictions))
        correct = confusion[0, 0] + confusion[1, 1]
        tpr = confusion[1, 1] / sum(labels == 1)
        fpr = confusion[0, 1] / sum(labels == 0)
        cells = [[i for i, t in enumerate(trials)
                  if t["recipe"] == recipe and t["sender"] == sender]
                 for recipe in frozen_analysis.plan.RECIPES for sender in ("alice", "bob")]
        exact = exact_tail(labels, predictions, cells)
        reported = claimed["results"][arm]
        numerical = reported["correct"] == correct and reported["trials"] == len(labels) \
            and abs(reported["accuracy"] - correct / len(labels)) < 1e-12 \
            and abs(reported["tpr"] - tpr) < 1e-12 and abs(reported["fpr"] - fpr) < 1e-12
        # Primary test uses 10,000 randomizations with a plus-one correction.
        tolerance = 5 * (exact * (1 - exact) / 10000) ** .5 + 2 / 10001
        mc_consistent = abs(reported["blocked_permutation_p"] - exact) <= tolerance
        checks[arm] = {
            "counts_match": bool(numerical), "monte_carlo_consistent": bool(mc_consistent),
            "correct": correct, "trials": len(labels), "tpr": float(tpr), "fpr": float(fpr),
            "exact_conditional_randomization_p": exact,
            "primary_monte_carlo_p": reported["blocked_permutation_p"],
            "monte_carlo_comparison_tolerance": tolerance,
        }
    aa = claimed["results"]["aa"]
    nominal = aa["blocked_permutation_p"] >= .05 and all(
        row["p_holm_confirmatory"] >= .05 for row in aa["confirmatory"])
    result = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "passed": all(r["passed"] for r in native)
                  and all(r["counts_match"] and r["monte_carlo_consistent"] for r in checks.values())
                  and claimed["aa_nominal_pass"] == nominal,
        "native_audit_rerun": native, "numerical_checks": checks,
        "aa_nominal_decision_matches_report": claimed["aa_nominal_pass"] == nominal,
        "primary_aa_null_passed": bool(nominal),
        "holdout_results_sha256": sha(output / "holdout-results.json"),
        "verification_freeze_sha256": sha(HERE / "VERIFICATION-FROZEN.json"),
        "scope": "Reuses frozen features/models and native auditor. Independent confusion arithmetic and exact classifier randomization. Does not independently reimplement every confirmatory feature test or replace the primary reporting family.",
    }
    with (HERE / "RESULT.json").open("x") as stream:
        json.dump(result, stream, indent=2)
    if not result["passed"]:
        raise RuntimeError("Retained verification failed; investigate before dependent stages")
    return result


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
