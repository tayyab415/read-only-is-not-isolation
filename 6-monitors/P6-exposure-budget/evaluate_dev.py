#!/usr/bin/env python3
"""Evaluate P6 and M1 on the fixed dev cohorts only."""
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path

from monitor import canonical_server_view, exposure_budget, m1_score

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
N_LADDER = LAB / "evidence" / "N-ladder"


def read_json(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def auc(labels, scores):
    positives = [score for label, score in zip(labels, scores) if label == 1]
    negatives = [score for label, score in zip(labels, scores) if label == 0]
    if not len(positives) or not len(negatives):
        return None
    ordered = sorted(range(len(scores)), key=lambda index: scores[index])
    ranks = [0.0] * len(scores)
    lo = 0
    while lo < len(scores):
        hi = lo + 1
        while hi < len(scores) and scores[ordered[hi]] == scores[ordered[lo]]:
            hi += 1
        for index in ordered[lo:hi]:
            ranks[index] = (lo + 1 + hi) / 2
        lo = hi
    positive_rank_sum = sum(rank for label, rank in zip(labels, ranks) if label == 1)
    return (
        positive_rank_sum - len(positives) * (len(positives) + 1) / 2
    ) / (len(positives) * len(negatives))


def quantile(values, probability):
    """Match NumPy's default linear quantile definition without NumPy."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metric(rows, label, score_key, threshold):
    labels = [row[label] for row in rows]
    scores = [float(row["scores"][score_key]) for row in rows]
    positives = [score for value, score in zip(labels, scores) if value == 1]
    negatives = [score for value, score in zip(labels, scores) if value == 0]
    return {
        "auc": auc(labels, scores),
        "recall": sum(score >= threshold for score in positives) / len(positives) if positives else None,
        "false_positive_rate": (
            sum(score >= threshold for score in negatives) / len(negatives) if negatives else None
        ),
    }


def bootstrap(rows, label, score_key, threshold, draws, seed):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["cohort"]].append(row)
    strata = defaultdict(list)
    for cohort, values in grouped.items():
        strata[values[0]["task_label"]].append(cohort)
    rng = random.Random(seed)
    estimates = {"auc": [], "recall": [], "false_positive_rate": []}
    for _ in range(draws):
        sample = []
        for cohort_ids in strata.values():
            for _ in cohort_ids:
                cohort = rng.choice(cohort_ids)
                sample.extend(grouped[cohort])
        values = metric(sample, label, score_key, threshold)
        for key, value in values.items():
            if value is not None:
                estimates[key].append(value)
    return {
        key: {
            "value": metric(rows, label, score_key, threshold)[key],
            "ci95": [quantile(values, 0.025), quantile(values, 0.975)],
            "defined_bootstrap_draws": len(values),
        }
        for key, values in estimates.items()
    }


def roc(rows, label, score_key):
    values = sorted({float(row["scores"][score_key]) for row in rows}, reverse=True)
    return [
        {
            "threshold": threshold,
            "true_positive_rate": metric(rows, label, score_key, threshold)["recall"],
            "false_positive_rate": metric(rows, label, score_key, threshold)["false_positive_rate"],
        }
        for threshold in values
    ]


def benign_by_variant(rows, score_key, threshold):
    result = {}
    for variant in sorted({row["variant"] for row in rows if not row["task_label"]}):
        matched = [row for row in rows if row["variant"] == variant]
        result[variant] = {
            "agents": len(matched),
            "flags": sum(row["scores"][score_key] >= threshold for row in matched),
        }
    return result


def main():
    config_path = HERE / "config.json"
    config = read_json(config_path)
    positives = set(config["dev_positive_cohorts"])
    negatives = set(config["dev_negative_cohorts"])
    if positives & negatives:
        raise RuntimeError("a cohort cannot be both dev positive and dev negative")
    if not all(cohort.startswith("a") for cohort in positives):
        raise RuntimeError("dev positives must be A-stage cohorts")
    if not all(cohort.startswith("bn-") for cohort in negatives):
        raise RuntimeError("dev negatives must be original BN cohorts")

    variants = {
        spec["cohort_id"]: spec["variants"]
        for spec in read_json(N_LADDER / "plan-BN.json")
    }
    rows, source_hashes = [], {}
    for cohort in [*config["dev_positive_cohorts"], *config["dev_negative_cohorts"]]:
        data_dir = N_LADDER / "data" / cohort
        server_path = data_dir / "server.json"
        server = read_json(server_path)
        source_hashes[str(server_path.relative_to(LAB))] = digest(server_path)
        principals = [f"n-{cohort}-alice", f"n-{cohort}-bob"]
        view = canonical_server_view(server["gateway_log"])
        p6_scores, p6_detail = exposure_budget(
            view, server["repo"], principals, config["negative_cache_window_ns"]
        )
        m1_scores = m1_score(
            view, server["repo"], principals, config["negative_cache_window_ns"]
        )

        # Labels join only after both server-only monitors have returned.
        verification_path = data_dir / "verification-v2.json"
        verification = read_json(verification_path)
        source_hashes[str(verification_path.relative_to(LAB))] = digest(verification_path)
        for agent, principal in zip(["alice", "bob"], principals):
            rows.append({
                "cohort": cohort,
                "agent": agent,
                "task_label": int(cohort in positives),
                "behaviour_label": int(verification["agents"][agent]["verified_exchange"]),
                "variant": variants.get(cohort, {}).get(agent),
                "scores": {"P6": p6_scores[principal], "M1": m1_scores[principal]},
                "p6": p6_detail[principal],
            })

    threshold = config["threshold_bits"]
    bootstrap_config = config["bootstrap"]
    result = {
        "format": "P6-dev-results-v1",
        "agents": len(rows),
        "cohorts": len({row["cohort"] for row in rows}),
        "threshold_bits": threshold,
        "score_rows": rows,
        "task_label": {},
        "behaviour_label": {},
        "source_sha256": source_hashes,
    }
    for label in ["task_label", "behaviour_label"]:
        for monitor in ["P6", "M1"]:
            result[label][monitor] = {
                **bootstrap(
                    rows, label, monitor, threshold,
                    bootstrap_config["draws"], bootstrap_config["seed"],
                ),
                "roc": roc(rows, label, monitor),
                "benign_by_variant": benign_by_variant(rows, monitor, threshold)
                if label == "task_label" else None,
            }
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
