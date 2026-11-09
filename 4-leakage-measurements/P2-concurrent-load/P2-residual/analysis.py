"""P2 analysis using P1 v2 features, with blocked-label randomization tests.

No holdout is opened by calibration or freeze. P2 is a separate experiment.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import pickle
from pathlib import Path
import sys

import numpy as np
from scipy.stats import binomtest, fisher_exact

import plan

HERE = Path(__file__).resolve().parent
P1 = HERE.parent / "P1-meter"
spec = importlib.util.spec_from_file_location("p1_core", P1 / "analysis.py")
core = importlib.util.module_from_spec(spec)
sys.modules["p1_core"] = core
spec.loader.exec_module(core)
PERMUTATIONS = 10000


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def features(trial, users):
    result = core.trial_features(trial, users)
    lags = []
    for row in trial["post"]:
        lag = float(row["start_lag_ms"])
        lags.append(lag)
        result[f'{row["probe"]}|start_lag_ms'] = lag
        result[f'{row["probe"]}|error'] = row.get("error", "none")
        result[f'{row["probe"]}|header_order'] = "|".join(
            name.lower() for name, _ in row.get("headers", []) if name.lower() not in core.EXCLUDED_HEADERS)
    if lags:
        result["fam.schedule|start_lag_ms|median"] = float(np.median(lags))
        result["fam.schedule|start_lag_ms|max"] = max(lags)
    return result


def blocked_permutation_p(pred, y, recipes, senders, permutations=PERMUTATIONS):
    """Shuffle within the recipe/direction cells used by the assignment."""
    rng = np.random.default_rng(plan.SEED)
    cells = [np.flatnonzero((recipes == recipe) & (senders == sender))
             for recipe in plan.RECIPES for sender in ("alice", "bob")]
    observed = int((pred == y).sum())
    exceed = 0
    for _ in range(permutations):
        shuffled = y.copy()
        for indices in cells:
            shuffled[indices] = rng.permutation(y[indices])
        exceed += int((pred == shuffled).sum()) >= observed
    return float((exceed + 1) / (permutations + 1))


def confirmations(X, y, recipes, enc, hypotheses):
    rows = []
    indices = {name: index for index, name in enumerate(enc.columns)}
    seen = set()
    for hypothesis in hypotheses:
        name, recipe = hypothesis["feature"], hypothesis["recipe"]
        if (name, recipe) in seen:
            raise ValueError("Duplicate frozen hypothesis")
        seen.add((name, recipe))
        subset = np.ones(len(y), dtype=bool) if recipe is None else recipes == recipe
        row = core.column_test(X[subset], y[subset], enc.columns, len(enc.numeric),
                               indices[name], y[subset] == 1)
        rows.append({**row, "recipe": recipe,
                     "comparison": "all_active" if recipe is None else "same_recipe"})
    for row, q in zip(rows, core.holm([row["p"] for row in rows])):
        row["p_holm_confirmatory"] = q
    return rows


def validate_sources():
    policy = json.loads((HERE / "SOURCES-FROZEN.json").read_text())
    for name, expected in policy["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"P2 source changed after freeze: {name}")
    if sha(P1 / "analysis.py") != policy["p1_analysis_sha256"]:
        raise RuntimeError("Imported P1 analysis changed")
    return policy


def prepare(data, configuration, split):
    folder = data / configuration
    users = json.loads((folder / f"{split}-provision.json").read_text())["users"]
    trials = core.load(folder / f"{split}.jsonl")
    expected = plan.schedule(configuration, split, 300 if split == "calibration" else 1000)
    if len(trials) != len(expected) or any(
            any(trial.get(key) != value for key, value in scheduled.items())
            for trial, scheduled in zip(trials, expected)):
        raise RuntimeError("Trial records do not match the frozen schedule")
    if any(trial.get("batch_must_stop") for trial in trials):
        raise RuntimeError("A batch failed; retained observations are not a completed verdict")
    return (trials, [features(trial, users) for trial in trials],
            np.array([trial["label"] for trial in trials]),
            np.array([trial["recipe"] for trial in trials]),
            np.array([trial["sender"] for trial in trials]))

def check_audit(data, split):
    record = json.loads((data / f"{split}-audit-results.json").read_text())
    count = 300 if split == "calibration" else 1000
    if not record["passed"] or record["smoke"] or record["source_sha256"] != sha(HERE / "audit.py") \
            or {row["configuration"] for row in record["runs"]} != set(plan.CONFIGURATIONS) \
            or any(not row["passed"] or row["trials"] != count or row["split"] != split
                   for row in record["runs"]):
        raise RuntimeError("A complete causal audit is required before analysis")
    return record


def calibrate(data, out):
    validate_sources()
    check_audit(data, "calibration")
    if any((data / name / "holdout.jsonl").exists() for name in plan.CONFIGURATIONS):
        raise RuntimeError("Holdout data must not exist before the calibration freeze")
    out.mkdir(parents=True, exist_ok=True)
    if (HERE / "FROZEN.json").exists():
        raise RuntimeError("The calibration is already frozen")
    summary = {}
    for configuration in plan.CONFIGURATIONS:
        trials, values, y, recipes, _ = prepare(data, configuration, "calibration")
        encoder = core.Encoder().fit(values, y)
        X = encoder.transform(values)
        name, model, cv = core.choose_model(X, y)
        overall = core.feature_tests(X, y, encoder.columns, len(encoder.numeric))
        by_recipe = {}
        for recipe in plan.RECIPES:
            subset = recipes == recipe
            by_recipe[recipe] = core.feature_tests(X[subset], y[subset], encoder.columns,
                                                  len(encoder.numeric))
        hypotheses = core.confirmation_hypotheses(overall, by_recipe)
        with (out / f"{configuration}.model.pkl").open("xb") as stream:
            pickle.dump({"encoder": encoder, "model": model, "name": name}, stream)
        summary[configuration] = {
            "trials": len(trials), "features": len(encoder.columns), "model": name,
            "cv_accuracy": cv, "selected_for_confirmation": hypotheses,
            "calibration_sha256": sha(data / configuration / "calibration.jsonl"),
            "provision_sha256": sha(data / configuration / "calibration-provision.json"),
        }
    with (out / "calibration-results.json").open("x") as stream:
        json.dump(summary, stream, indent=2)
    return summary


def freeze(data, out):
    validate_sources()
    check_audit(data, "calibration")
    calibration = json.loads((out / "calibration-results.json").read_text())
    if set(calibration) != set(plan.CONFIGURATIONS):
        raise RuntimeError("All four calibration fits are required")
    files = [out / "calibration-results.json", HERE / "SOURCES-FROZEN.json",
             data / "calibration-audit-results.json"]
    for configuration, result in calibration.items():
        if result["trials"] != 300:
            raise RuntimeError("Incorrect calibration size")
        for name, key in [("calibration.jsonl", "calibration_sha256"),
                          ("calibration-provision.json", "provision_sha256")]:
            path = data / configuration / name
            if sha(path) != result[key]:
                raise RuntimeError("Calibration input changed")
            files.append(path)
        files.append(out / f"{configuration}.model.pkl")
    record = {"utc": datetime.now(timezone.utc).isoformat(),
              "data": str(data.resolve().relative_to(HERE)),
              "out": str(out.resolve().relative_to(HERE)),
              "sha256": {str(path.resolve().relative_to(HERE)): sha(path) for path in files},
              "holdout_collection_allowed": True}
    with (HERE / "FROZEN.json").open("x") as stream:
        json.dump(record, stream, indent=2)
    return record


def holdout(data, out):
    validate_sources()
    check_audit(data, "holdout")
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    if data.resolve() != (HERE / frozen["data"]).resolve() \
            or out.resolve() != (HERE / frozen["out"]).resolve():
        raise RuntimeError("Holdout path differs from the model freeze")
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Frozen fit changed: {name}")
    calibration = json.loads((out / "calibration-results.json").read_text())
    results = {}
    for configuration in plan.CONFIGURATIONS:
        folder = data / configuration
        seal = json.loads((folder / "holdout-sealed.json").read_text())
        if not seal["complete"] or seal["trials"] != 1000 or not seal["cleanup_absence_proved"] \
                or sha(folder / "holdout.jsonl") != seal["sha256"] \
                or sha(folder / "holdout-provision.json") != seal["provision_sha256"]:
            raise RuntimeError("Holdout inputs differ from their seals")
        trials, values, y, recipes, senders = prepare(data, configuration, "holdout")
        with (out / f"{configuration}.model.pkl").open("rb") as stream:
            fit = pickle.load(stream)
        encoder, model = fit["encoder"], fit["model"]
        X = encoder.transform(values)
        pred = model.predict(X)
        correct = int((pred == y).sum())
        conf = confirmations(X, y, recipes, encoder,
                             calibration[configuration]["selected_for_confirmation"])
        per_recipe = {}
        for recipe in plan.RECIPES:
            active, idle = (recipes == recipe) & (y == 1), (recipes == recipe) & (y == 0)
            table = [[int(pred[active].sum()), int(active.sum() - pred[active].sum())],
                     [int(pred[idle].sum()), int(idle.sum() - pred[idle].sum())]]
            per_recipe[recipe] = {
                "active": int(active.sum()), "idle": int(idle.sum()),
                "tpr": float(pred[active].mean()), "fpr": float(pred[idle].mean()),
                "fisher_p": float(fisher_exact(table, alternative="greater").pvalue)}
        interval = binomtest(correct, len(y)).proportion_ci(confidence_level=0.9875)
        results[configuration] = {
            "trials": len(y), "correct": correct, "accuracy": correct / len(y),
            "tpr": float(pred[y == 1].mean()), "fpr": float(pred[y == 0].mean()),
            "blocked_permutation_p": blocked_permutation_p(pred, y, recipes, senders),
            "binomial_interval_98_75": [float(interval.low), float(interval.high)],
            "interval_assumption": "Independent Bernoulli outcomes; four Bonferroni intervals.",
            "confirmatory": conf, "per_recipe": per_recipe,
            "sender_overrun_trials": sum(trial["sender_overrun_ms"] > 0 for trial in trials),
            "late_receiver_dispatch_trials": sum(trial["receiver_schedule_lead_ms"] < 0 for trial in trials),
            "receiver_schedule_lead_ms_min": min(trial["receiver_schedule_lead_ms"] for trial in trials),
            "retained_collection_span_seconds": (
                trials[-1]["controller_end_utc_ns"] - trials[0]["controller_start_utc_ns"]) / 1e9,
            "rate_interpretation": "Collection span includes trial overhead and inter-trial gaps; no achieved binary protocol is inferred."}
    for (name, result), q in zip(results.items(), core.holm(
            [result["blocked_permutation_p"] for result in results.values()])):
        result["classifier_p_holm_experiment"] = q
    all_conf = [row for result in results.values() for row in result["confirmatory"]]
    for row, q in zip(all_conf, core.holm([row["p"] for row in all_conf])):
        row["p_holm_experiment"] = q
    all_recipes = [row for result in results.values() for row in result["per_recipe"].values()]
    for row, q in zip(all_recipes, core.holm([row["fisher_p"] for row in all_recipes])):
        row["p_holm_experiment"] = q
    aa = results["aa"]
    output = {"results": results, "aa_nominal_pass":
              aa["blocked_permutation_p"] >= 0.05 and not any(
                  row["p_holm_confirmatory"] < 0.05 for row in aa["confirmatory"]),
              "sham_is_zero_leak_null": False,
              "causal_audit_required": True,
              "scope": "Frozen-decoder association under the bounded schedule; not maximum capacity."}
    with (out / "holdout-results.json").open("x") as stream:
        json.dump(output, stream, indent=2)
    return output


if __name__ == "__main__":
    operation, data, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    action = {"calibrate": calibrate, "freeze": freeze, "holdout": holdout}[operation]
    result = action(data, out)
    print(json.dumps({"operation": operation, "completed": True}))
