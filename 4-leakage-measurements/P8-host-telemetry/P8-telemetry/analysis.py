"""Freeze all P8 fits before holdout; report one corrected four-arm family."""
import argparse
import json
from pathlib import Path
import statistics
import time

from scipy.stats import beta

from experiment import CONFIGURATIONS, SEED, blocked_pvalue, cpu_feature, digest, fit, holm, predict
from collector import verify_freeze
import runtime

HERE = Path(__file__).resolve().parent


def read_records(data, configuration, split):
    return [json.loads(line) for line in (data / configuration / f"{split}.jsonl").read_text().splitlines()]


def checked_audit(data, split):
    path = data / f"{split}-audit-results.json"
    audit = json.loads(path.read_text())
    expected = {"calibration": 24, "holdout": 64}[split]
    if audit.get("passed") is not True or audit["split"] != split or len(audit["runs"]) != 4 \
            or set(row["configuration"] for row in audit["runs"]) != set(CONFIGURATIONS) \
            or any(not row["passed"] or row["trials"] != expected for row in audit["runs"]) \
            or audit["audit_source_sha256"] != runtime.sha(HERE / "audit.py"):
        raise RuntimeError("P8 requires complete audited inputs")
    for row in audit["runs"]:
        folder = data / row["configuration"]
        if runtime.sha(folder / f"{split}.jsonl") != row["observation_sha256"] \
                or runtime.sha(folder / f"{split}-sealed.json") != row["seal_sha256"]:
            raise RuntimeError("Audited P8 observations changed")
    return audit


def interval(correct, count):
    return [float(beta.ppf(.005, correct, count - correct + 1)) if correct else 0.0,
            float(beta.ppf(.995, correct + 1, count - correct)) if correct < count else 1.0]


def calibrate(data, output):
    runtime.check_sources()
    checked_audit(data, "calibration")
    if (HERE / "FROZEN.json").exists() or any((data / arm / "holdout.jsonl").exists() for arm in CONFIGURATIONS):
        raise RuntimeError("All models must be frozen before any P8 holdout")
    output.mkdir(parents=True, exist_ok=True)
    artifacts = [HERE / "SOURCES-FROZEN.json", data / "calibration-audit-results.json"]
    models = {}
    for configuration in CONFIGURATIONS:
        records = read_records(data, configuration, "calibration")
        models[configuration] = fit(records)
        for name in ("calibration.jsonl", "calibration-schedule.json", "calibration-provision.json",
                     "calibration-readiness.json", "calibration-server.json", "calibration-cleanup.json",
                     "calibration-sealed.json"):
            artifacts.append(data / configuration / name)
    result = {"models": models, "family": list(CONFIGURATIONS), "primary_tests": 4,
              "feature": "aggregate_busy_fraction_first_to_last", "permutations": 9999,
              "family_alpha": .05, "correction": "Holm"}
    model_path = output / "calibration-models.json"
    with model_path.open("x") as stream:
        json.dump(result, stream, indent=2)
    artifacts.append(model_path)
    frozen = {"utc_ns": time.time_ns(), "data": str(data.resolve().relative_to(HERE.resolve())),
              "models": str(model_path.resolve().relative_to(HERE.resolve())),
              "holdout_collection_allowed": True, "all_models_frozen_before_holdout": True,
              "sha256": {str(path.resolve().relative_to(HERE.resolve())): runtime.sha(path) for path in artifacts}}
    with (HERE / "FROZEN.json").open("x") as stream:
        json.dump(frozen, stream, indent=2)
    return result


def holdout(data, output):
    runtime.check_sources()
    frozen = verify_freeze(data)
    audit = checked_audit(data, "holdout")
    models = json.loads((HERE / frozen["models"]).read_text())
    results, pvalues = {}, {}
    for configuration in CONFIGURATIONS:
        rows = read_records(data, configuration, "holdout")
        model = models["models"][configuration]
        values = [cpu_feature(row["receiver"]["samples"])["value"] for row in rows]
        predictions = [predict(model, value) for value in values]
        labels, blocks = [row["label"] for row in rows], [row["block"] for row in rows]
        correct = sum(prediction == label for prediction, label in zip(predictions, labels))
        tp = sum(prediction == label == 1 for prediction, label in zip(predictions, labels))
        fp = sum(prediction == 1 and label == 0 for prediction, label in zip(predictions, labels))
        fn, tn = labels.count(1) - tp, labels.count(0) - fp
        pvalues[configuration] = blocked_pvalue(predictions, labels, blocks, seed=SEED +
                                               CONFIGURATIONS.index(configuration))
        sender_cpu = [row["sender"]["load"]["process_cpu_ns"] / 1e9 for row in rows]
        durations = [(row["controller_end_utc_ns"] - row["controller_start_utc_ns"]) / 1e9 for row in rows]
        by_label = {str(label): {
            "busy_fraction_median": statistics.median(value for value, truth in zip(values, labels) if truth == label),
            "sender_process_cpu_seconds_median": statistics.median(value for value, truth in
                                                                  zip(sender_cpu, labels) if truth == label)}
                    for label in (0, 1)}
        results[configuration] = {
            "trials": len(rows), "correct": correct, "accuracy": correct / len(rows),
            "accuracy_99pct_iid_binomial_interval": interval(correct, len(rows)),
            "interval_assumption": "IID Bernoulli accuracy; does not remove shared-host serial dependence",
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "true_positive_rate": tp / (tp + fn), "false_positive_rate": fp / (fp + tn),
            "uncorrected_blocked_pvalue": pvalues[configuration], "model": model,
            "by_label": by_label, "trial_wall_seconds_median": statistics.median(durations),
            "trial_wall_seconds_total": sum(durations), "predictions": predictions,
            "counter_feature_values": values, "labels": labels}
    corrected = holm(pvalues)
    for configuration in CONFIGURATIONS:
        results[configuration]["holm_pvalue"] = corrected[configuration]
    null_gate = all(corrected[arm] >= .05 for arm in ("aa-native", "aa-masked"))
    result = {"utc_ns": time.time_ns(), "results": results, "null_gate_passed": null_gate,
              "family": list(CONFIGURATIONS), "primary_tests": 4, "correction": "Holm",
              "permutations": 9999, "audit_passed": True,
              "model_freeze_sha256": runtime.sha(HERE / "FROZEN.json"),
              "audit_sha256": runtime.sha(data / "holdout-audit-results.json"),
              "scope": "Fixed scripted host-counter decoder; no model, general timing isolation, or capacity claim."}
    output.mkdir(parents=True, exist_ok=True)
    with (output / "holdout-results.json").open("x") as stream:
        json.dump(result, stream, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("calibrate", "holdout"))
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = calibrate(args.data.resolve(), args.output.resolve()) if args.mode == "calibrate" \
        else holdout(args.data.resolve(), args.output.resolve())
    print(json.dumps({"mode": args.mode, "null_gate_passed": result.get("null_gate_passed")}))


if __name__ == "__main__":
    main()
