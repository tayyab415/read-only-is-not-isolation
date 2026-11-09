"""Four frozen timing decoders, with exact binomial and bounded claims."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
from fractions import Fraction

import plan

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def check_sources():
    freeze = json.loads((HERE / "SOURCES-FROZEN.json").read_text())
    for name, expected in freeze["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError("P7 analysis source changed")
    return freeze


def wilson(successes, total):
    z = 1.959963984540054
    center = (successes / total + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(successes / total * (1 - successes / total) / total
                         + z * z / (4 * total * total)) / (1 + z * z / total)
    return [center - half, center + half]


def holm(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    output, previous = [None] * len(values), 0
    for rank, index in enumerate(order):
        previous = max(previous, min(1, values[index] * (len(values) - rank)))
        output[index] = previous
    return output


def decode(model, elapsed_ms):
    return int(elapsed_ms < model["threshold_ms"]) if model["active_is_faster"] \
        else int(elapsed_ms > model["threshold_ms"])


def randomization_p(trials, predictions):
    """Exact label randomization, preserving balance within receiver direction."""
    distribution = {0: Fraction(1)}
    observed = sum(row["label"] == prediction for row, prediction in zip(trials, predictions))
    for actor in ("alice", "bob"):
        group = [(row["label"], prediction) for row, prediction in zip(trials, predictions) if row["receiver"] == actor]
        total, positive_labels, positive_predictions = len(group), sum(label for label, _ in group), sum(prediction for _, prediction in group)
        if not total or positive_labels * 2 != total:
            raise ValueError("Direction must have balanced randomized labels")
        possible = {}
        for true_positive in range(max(0, positive_predictions - (total - positive_labels)),
                                   min(positive_labels, positive_predictions) + 1):
            correct = total - positive_labels - positive_predictions + 2 * true_positive
            possible[correct] = Fraction(math.comb(positive_labels, true_positive)
                * math.comb(total - positive_labels, positive_predictions - true_positive),
                math.comb(total, positive_predictions))
        combined = {}
        for left, first in distribution.items():
            for right, second in possible.items():
                combined[left + right] = combined.get(left + right, Fraction(0)) + first * second
        distribution = combined
    return float(sum(probability for correct, probability in distribution.items() if correct >= observed))


def fit(trials):
    times = {label: [row["receiver_request"]["elapsed_ms"] for row in trials if row["label"] == label]
             for label in (0, 1)}
    if len(trials) != 12 or len(times[0]) != len(times[1]) or len(times[0]) != 6:
        raise ValueError("Incomplete balanced calibration")
    idle, active = statistics.median(times[0]), statistics.median(times[1])
    return {"threshold_ms": (idle + active) / 2, "active_is_faster": active < idle,
            "calibration_idle_median_ms": idle, "calibration_active_median_ms": active,
            "observed_feature": "receiver elapsed_ms only"}


def freeze_models():
    check_sources()
    if (HERE / "FROZEN.json").exists() or (HERE / "data/g2-main").exists():
        raise RuntimeError("Do not fit or refreeze after holdout collection")
    data = HERE / "data/g2-cal"
    audit = json.loads((data / "audit-results.json").read_text())
    expected = {(product, delay) for product in plan.PRODUCTS for delay in plan.DELAYS}
    if not audit["passed"] or audit["audit_source_sha256"] != sha(HERE / "audit.py") \
            or {(row["product"], row["delay_ms"]) for row in audit["cohorts"]} != expected \
            or len(audit["cohorts"]) != 4 or any(not row["passed"] or row["split"] != "calibration"
                or row["arm"] != "shared" or row["completed"] != 12 for row in audit["cohorts"]):
        raise RuntimeError("All four complete calibrations must pass the independent audit")
    models, inputs = {}, {"data/g2-cal/audit-results.json": sha(data / "audit-results.json")}
    folder = HERE / "models/g2"
    folder.mkdir(parents=True, exist_ok=True)
    for product, delay in sorted(expected):
        cohort = data / f"{product}-{delay}-shared"
        seal = json.loads((cohort / "sealed.json").read_text())
        audited = next(row for row in audit["cohorts"] if row["product"] == product and row["delay_ms"] == delay)
        if not seal["complete"] or audited["sealed_sha256"] != sha(cohort / "sealed.json") \
                or any(sha(cohort / name) != expected_hash for name, expected_hash in seal["sha256"].items()):
            raise RuntimeError("Audited P7 calibration changed")
        model = {**fit(rows(cohort / "trials.jsonl")), "product": product, "delay_ms": delay,
                 "source_freeze_sha256": sha(HERE / "SOURCES-FROZEN.json"),
                 "calibration_seal_sha256": sha(cohort / "sealed.json")}
        path = folder / f"{product}-{delay}.json"
        if path.exists():
            raise RuntimeError("Preserve prior decoder files after an interrupted freeze")
        path.write_text(json.dumps(model, indent=2) + "\n")
        models[path.relative_to(HERE).as_posix()] = sha(path)
        for name in [*seal["sha256"], "sealed.json"]:
            inputs[(cohort / name).relative_to(HERE).as_posix()] = sha(cohort / name)
    future = {f"{product}-{delay}": {"arm_order": plan.arm_order(product, delay),
               "schedule": {arm: plan.schedule(product, delay, arm, "holdout") for arm in (*plan.ARMS, "no-sender")}}
              for product, delay in sorted(expected)}
    freeze = {"edition": 1, "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "source_freeze_sha256": sha(HERE / "SOURCES-FROZEN.json"), "calibration_sha256": inputs,
        "model_sha256": models, "prospective_holdout": future,
        "primary_family": "Four shared-cache tests, product by delay; one-sided exact label randomization within receiver direction, Holm alpha 0.05.",
        "defended_timing": "Exploratory; fresh targeted intervention required before attribution."}
    (HERE / "FROZEN.json").write_text(json.dumps(freeze, indent=2) + "\n")
    return {"frozen": True, "sha256": sha(HERE / "FROZEN.json"), "models": 4, "holdout_opened": False}


def holdout():
    check_sources()
    from collect import holdout_gate
    frozen = holdout_gate()
    data = HERE / "data/g2-main"
    audit = json.loads((data / "audit-results.json").read_text())
    if not audit["passed"] or audit["audit_source_sha256"] != sha(HERE / "audit.py") \
            or len(audit["cohorts"]) != 20:
        raise RuntimeError("Complete main evidence must pass audit before interpretation")
    results = []
    for product in plan.PRODUCTS:
        for delay in plan.DELAYS:
            model = json.loads((HERE / f"models/g2/{product}-{delay}.json").read_text())
            for arm in (*plan.ARMS, "no-sender"):
                folder = data / f"{product}-{delay}-{arm}"
                seal = json.loads((folder / "sealed.json").read_text())
                audited = next(row for row in audit["cohorts"]
                    if row["product"] == product and row["delay_ms"] == delay and row["arm"] == arm)
                if not audited["passed"] or audited["sealed_sha256"] != sha(folder / "sealed.json") \
                        or any(sha(folder / name) != expected for name, expected in seal["sha256"].items()):
                    raise RuntimeError("Audited P7 main evidence changed")
                trials = rows(folder / "trials.jsonl")
                public = json.loads((folder / "provision.json").read_text())
                serving = json.loads((folder / "server.json").read_text())
                if [{key: row[key] for key in plan.schedule(product, delay, arm, "holdout")[0]} for row in trials] \
                        != frozen["prospective_holdout"][f"{product}-{delay}"]["schedule"][arm]:
                    raise RuntimeError("Main P7 assignment differs from frozen schedule")
                confusion = {"00": 0, "01": 0, "10": 0, "11": 0}
                predictions = []
                for row in trials:
                    prediction = decode(model, row["receiver_request"]["elapsed_ms"])
                    predictions.append(prediction)
                    confusion[f"{row['label']}{prediction}"] += 1
                correct, total = confusion["00"] + confusion["11"], len(trials)
                result = {**{key: audited[key] for key in ("product", "delay_ms", "arm", "split")},
                    "trials": total, "correct": correct, "accuracy": correct / total,
                    "wilson95_accuracy": wilson(correct, total), "confusion_true_then_prediction": confusion,
                    "one_sided_blocked_randomization_p": randomization_p(trials, predictions),
                    "idle_receiver_median_ms": statistics.median(row["receiver_request"]["elapsed_ms"]
                                                               for row in trials if row["label"] == 0),
                    "active_label_receiver_median_ms": statistics.median(row["receiver_request"]["elapsed_ms"]
                                                                         for row in trials if row["label"] == 1),
                    "controller_http_requests": audited["controller_http_requests"],
                    "origin_http_requests": audited["origin_http_requests"],
                    "total_http_requests": audited["total_http_requests"],
                    "resource_snapshots": {name: public[name] for name in ("resource_before", "resource_after_live")},
                    "cache_directory_inventories": {name: value["cache_inventory"] for name, value in serving["caches"].items()},
                    "cache_image_logical_size_bytes": public["build"]["image_logical_size_bytes"],
                    "body_bytes_to_clients": sum(row["body_bytes"] for row in rows(folder / "requests.jsonl") if row["execute"] == 1),
                    "body_bytes_from_origin": sum(row["body_bytes"] for row in serving["origin_log"] if row.get("marker")),
                    "application_cache_cause_audited": True, "model_sha256": sha(HERE / f"models/g2/{product}-{delay}.json")}
                if arm == "no-sender":
                    result.update(no_sender_false_positive_count=sum(predictions),
                                  no_sender_false_positive_fraction=sum(predictions) / total,
                                  no_sender_false_positive_wilson95=wilson(sum(predictions), total))
                results.append(result)
    positive = [row for row in results if row["arm"] == "shared"]
    for row, adjusted in zip(positive, holm([row["one_sided_blocked_randomization_p"] for row in positive])):
        row.update(primary_holm_p=adjusted, shared_timing_decoder_confirmed=adjusted < .05)
    ledger = rows(HERE / "data/g1-request-ledger.jsonl")
    if len(ledger) > 1200 or sum(row["reserved_http_requests"] for row in ledger) > 2400:
        raise RuntimeError("Global request limit exceeded")
    return {"source_freeze_sha256": sha(HERE / "SOURCES-FROZEN.json"), "model_freeze_sha256": sha(HERE / "FROZEN.json"),
        "audit_sha256": sha(data / "audit-results.json"), "results": results,
        "all_four_shared_timing_controls_confirmed": all(row["shared_timing_decoder_confirmed"] for row in positive),
        "controller_reserved_requests": len(ledger), "reserved_http_upper_bound": sum(row["reserved_http_requests"] for row in ledger),
        "limitations": ["One fixed timing decoder per product/delay, 32 trials per arm, 16 no-sender trials.",
            "Configured negative caching, including nondefault Squid build and TTL.",
            "Generic HTTP caches, not native package services or a deployment of the full P1 meter.",
            "Wilson intervals are approximate binomial descriptions; shared-host trials do not give independent-deployment intervals.",
            "Application-cache causes are independently joined; defended timing is exploratory.",
            "Chance-like decoding does not bound capacity; private instances still share their host."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("freeze", "holdout"))
    args = parser.parse_args()
    result = freeze_models() if args.mode == "freeze" else holdout()
    if args.mode == "holdout":
        (HERE / "data/g2-main/holdout-results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
