"""Fixed P8 schedule and decoder, independent of deployment."""
import hashlib
import json
import math
import random

from observer_contract import OFFSETS, PAYLOAD, FILE_BYTES, CPU_BYTES, parse_cpu

CONFIGURATIONS = ("native", "masked", "aa-native", "aa-masked")
SEED = 2026110808


def digest(value):
    return hashlib.sha256(value).hexdigest()


def masked(configuration):
    return configuration in ("masked", "aa-masked")


def executing(configuration, label):
    return int(configuration in ("native", "masked") and label == 1)


def schedule(configuration, split, count=None):
    if configuration not in CONFIGURATIONS or split not in ("smoke", "calibration", "holdout"):
        raise ValueError("Unknown fixed P8 cell")
    expected = {"smoke": 4, "calibration": 24, "holdout": 64}[split]
    if count is not None and count != expected:
        raise ValueError("The prospective denominator cannot change")
    seed = SEED + 1000 * CONFIGURATIONS.index(configuration) + \
        100 * ("smoke", "calibration", "holdout").index(split)
    generator = random.Random(seed)
    records = []
    for block in range(expected // 4):
        labels = [0, 0, 1, 1]
        generator.shuffle(labels)
        for position, label in enumerate(labels):
            index = len(records)
            records.append({"trial_id": f"{split[0]}{index:04d}", "block": block,
                            "position": position, "label": label,
                            "token": digest(f"{seed}:{index}".encode())[:16]})
    return records


def counters(values):
    total = sum(values[:8])
    return total, total - values[3] - values[4]


def cpu_feature(samples):
    first = counters(parse_cpu(samples[0]["cpu_line"]))
    last = counters(parse_cpu(samples[-1]["cpu_line"]))
    total, busy = last[0] - first[0], last[1] - first[1]
    if total < 0 or busy < 0 or busy > total:
        raise ValueError("Invalid aggregate accounting delta")
    return {"value": busy / total if total else 0.0, "live_counter": total > 0,
            "total_delta": total, "busy_delta": busy,
            "iowait_delta": parse_cpu(samples[-1]["cpu_line"])[4] -
                            parse_cpu(samples[0]["cpu_line"])[4]}


def predict(model, value):
    if model["kind"] == "constant":
        return model["value"]
    return int(value >= model["threshold"]) if model["direction"] == 1 \
        else int(value < model["threshold"])


def fit(records):
    values = [cpu_feature(row["receiver"]["samples"])["value"] for row in records]
    labels = [row["label"] for row in records]
    unique = sorted(set(values))
    candidates = [{"kind": "constant", "value": 0}, {"kind": "constant", "value": 1}]
    for low, high in zip(unique, unique[1:]):
        for direction in (1, -1):
            candidates.append({"kind": "threshold", "threshold": (low + high) / 2,
                               "direction": direction})
    best, score = candidates[0], -1
    for candidate in candidates:
        correct = sum(predict(candidate, value) == label for value, label in zip(values, labels))
        if correct > score:
            best, score = candidate, correct
    return {**best, "calibration_correct": score, "calibration_trials": len(records),
            "feature": "aggregate_busy_fraction_first_to_last"}


def blocked_pvalue(predictions, labels, blocks, permutations=9999, seed=SEED):
    if not (len(predictions) == len(labels) == len(blocks)) or not predictions:
        raise ValueError("Incomplete randomized assignment")
    groups = [[index for index, block in enumerate(blocks) if block == key]
              for key in sorted(set(blocks))]
    if any(len(group) != 4 or sorted(labels[index] for index in group) != [0, 0, 1, 1]
           for group in groups):
        raise ValueError("Broken four-trial randomization block")
    observed = sum(prediction == label for prediction, label in zip(predictions, labels))
    generator = random.Random(seed)
    exceed = 0
    for _ in range(permutations):
        shuffled = labels[:]
        for group in groups:
            values = [labels[index] for index in group]
            generator.shuffle(values)
            for index, value in zip(group, values):
                shuffled[index] = value
        score = sum(prediction == label for prediction, label in zip(predictions, shuffled))
        exceed += score >= observed
    return (1 + exceed) / (1 + permutations)


def holm(values):
    result, prior = {}, 0.0
    for index, (name, value) in enumerate(sorted(values.items(), key=lambda pair: pair[1])):
        prior = max(prior, min(1.0, value * (len(values) - index)))
        result[name] = prior
    return result


def static_view(cpu_count):
    if not isinstance(cpu_count, int) or not 1 <= cpu_count <= 512:
        raise ValueError("Unsupported host CPU inventory")
    return ("cpu  0 0 0 1000000 0 0 0 0 0 0\n" + "".join(
        f"cpu{index} 0 0 0 1000000 0 0 0 0 0 0\n" for index in range(cpu_count))).encode()
