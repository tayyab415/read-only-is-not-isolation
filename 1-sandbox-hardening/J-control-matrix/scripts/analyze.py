#!/usr/bin/env python3
"""Compute measured outcomes without changing decoders or deleting failures."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def wilson(success, total):
    if not total:
        return None
    z = 1.959963984540054
    p = success / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def summarize(data):
    observed = [r for r in data if r["decoded_bit"] is not None]
    correct = sum(r["decoded_bit"] == r["expected_bit"] for r in observed)
    confusion = {f"expected_{a}_decoded_{b}": sum(
        r["expected_bit"] == a and r["decoded_bit"] == b for r in observed)
        for a in (0, 1) for b in (0, 1)}
    return {
        "trials": len(data), "observed": len(observed), "unavailable": len(data) - len(observed),
        "correct": correct, "accuracy": correct / len(observed) if observed else None,
        "wilson_95_accuracy": wilson(correct, len(observed)),
        "expected_ones": sum(r["expected_bit"] for r in data), **confusion,
        "decoded_ones": sum(r["decoded_bit"] == 1 for r in observed),
    }


def analyze(root):
    timing = rows(root / "timing-trials.jsonl")
    stat = rows(root / "stat-trials.jsonl")
    upstream = rows(root / "logs/j-upstream.jsonl")
    upstream_by_path = {}
    for row in upstream:
        upstream_by_path.setdefault(row["path"].split("?", 1)[0].lstrip("/"), []).append(row)
    request_rows = rows(root / "requests.jsonl")
    requests_by_label = {row["label"]: row for row in request_rows}
    output = {"timing": {}, "statistics": {}, "followup": {}}
    for delay in (150, 20):
        output["timing"][str(delay)] = {}
        arms = sorted(set(r["arm"] for r in timing if r["phase"] == "holdout"))
        for arm in arms:
            data = [r for r in timing if r["phase"] == "holdout" and r["arm"] == arm and r["delay_ms"] == delay]
            summary = summarize(data)
            summary["median_ms_by_bit"] = {
                str(bit): statistics.median(r["duration_ns"] / 1e6 for r in data
                                            if r["expected_bit"] == bit)
                for bit in (0, 1)
            }
            summary["upstream_counts_per_trial_by_bit"] = {
                str(bit): dict(sorted(Counter(
                    len(upstream_by_path.get(r["relative_path"], []))
                    for r in data if r["expected_bit"] == bit).items()))
                for bit in (0, 1)
            }
            summary["upstream_requests"] = sum(len(upstream_by_path.get(r["relative_path"], [])) for r in data)
            summary["receiver_upstream_counts_by_bit"] = {
                str(bit): dict(sorted(Counter(
                    sum(requests_by_label[r["receiver_label"]]["start_utc_ns"]
                        <= entry["start_utc_ns"]
                        <= requests_by_label[r["receiver_label"]]["end_utc_ns"]
                        for entry in upstream_by_path.get(r["relative_path"], []))
                    for r in data if r["expected_bit"] == bit).items()))
                for bit in (0, 1)
            }
            summary["upstream_methods"] = dict(Counter(
                entry["method"] for r in data for entry in upstream_by_path.get(r["relative_path"], [])))
            summary["status_counts"] = dict(Counter(r["status"] for r in data))
            output["timing"][str(delay)][arm] = summary
    for phase in ("no-sender", "repeat", "head", "denied-prime"):
        for delay, arm in sorted(set((r["delay_ms"], r["arm"]) for r in timing if r["phase"] == phase)):
            data = [r for r in timing if r["phase"] == phase and r["delay_ms"] == delay and r["arm"] == arm]
            output["followup"][f"{phase}-{delay}-{arm}"] = summarize(data)
    for arm in sorted(set(r["arm"] for r in stat)):
        data = [r for r in stat if r["arm"] == arm]
        summary = summarize(data)
        summary["statistics_status_counts"] = dict(Counter(
            status for r in data for status in (r["before_status"], r["after_status"])))
        output["statistics"][arm] = summary
    signal_labels = [r["sender_label"] for r in timing if r["sender_label"]]
    signal_records = [r for r in request_rows if r["label"] in set(signal_labels)]
    output["method_alert"] = {
        "scope": "Labelled timing sender requests only, not general coordination detection",
        "sender_requests": len(signal_records),
        "mutation_method_alerts": sum(r["method"] not in ("GET", "HEAD", "OPTIONS") for r in signal_records),
        "method_counts": dict(Counter(r["method"] for r in signal_records)),
        "status_counts": dict(Counter(r["status"] for r in signal_records)),
    }
    output["utility"] = json.loads((root / "utility.json").read_text())
    output["collection"] = json.loads((root / "collection-summary.json").read_text())
    challenges = rows(root / "followup-timing.jsonl")
    output["challenges"] = {}
    for phase, delay, arm in sorted(set((r["phase"], r["delay_ms"], r["arm"]) for r in challenges)):
        data = [r for r in challenges if (r["phase"], r["delay_ms"], r["arm"]) == (phase, delay, arm)]
        summary = summarize(data)
        summary["receiver_upstream_counts_by_bit"] = {
            str(bit): dict(sorted(Counter(
                sum(requests_by_label[r["receiver_label"]]["start_utc_ns"]
                    <= entry["start_utc_ns"]
                    <= requests_by_label[r["receiver_label"]]["end_utc_ns"]
                    for entry in upstream_by_path.get(r["relative_path"], []))
                for r in data if r["expected_bit"] == bit).items()))
            for bit in (0, 1)
        }
        output["challenges"][f"{phase}-{delay}-{arm}"] = summary
    counters = rows(root / "counter-followup.jsonl")
    output["counter_methods"] = {}
    for mode in sorted(set(r["mode"] for r in counters)):
        data = [r for r in counters if r["mode"] == mode]
        summary = summarize(data)
        summary["sender_status_counts"] = dict(Counter(r["sender_status"] for r in data if r["sender_label"]))
        summary["sender_body_byte_counts"] = dict(Counter(r["sender_body_bytes"] for r in data if r["sender_label"]))
        output["counter_methods"][mode] = summary
    noise = [r for r in request_rows if r["label"].startswith("noise-") and r["label"] != "noise-prewarm"]
    loaded = [r for r in challenges if r["phase"] == "load"]
    output["load"] = {
        "noise_requests": len(noise), "noise_status_counts": dict(Counter(r["status"] for r in noise)),
        "receivers_with_noise_request_interval_overlap": sum(any(
            n["start_utc_ns"] <= requests_by_label[r["receiver_label"]]["end_utc_ns"] and
            requests_by_label[r["receiver_label"]]["start_utc_ns"] <= n["end_utc_ns"] for n in noise)
            for r in loaded),
        "loaded_receiver_trials": len(loaded),
        "note": "Interval overlap is workload context, not attribution of one request to another.",
    }
    identities = rows(root / "identity-trials.jsonl")
    output["identity"] = {}
    for arm in sorted(set(r["arm"] for r in identities)):
        data = [r for r in identities if r["arm"] == arm]
        output["identity"][arm] = {
            "trials": len(data), "borrowed_status_counts": dict(Counter(r["borrowed_status"] for r in data)),
            "own_status_counts": dict(Counter(r["own_status"] for r in data)),
            "own_body_hashes": sorted(set(r["own_body_sha256"] for r in data)),
        }
    output["identity_negative_controls"] = rows(root / "identity-negative-controls.jsonl")
    package_trials = rows(root / "package-trials.jsonl")
    output["package_utility"] = {
        "trials": len(package_trials),
        "download_successes": sum(r["download_status"] == 200 for r in package_trials),
        "install_successes": sum(r["pip_exit_code"] == 0 and r["package_function_passed"] is True
                                 and r["installed_version"] == "1.16.0" for r in package_trials),
        "download_hashes": sorted(set(r["download_sha256"] for r in package_trials)),
    }
    query = json.loads((root / "query-audit.json").read_text())
    correlations_by_label = {r["label"]: r for r in json.loads((root / "log-matches.json").read_text())}
    labels = [query["info_label"], query["stats_label"]]
    output["query_audit"] = {
        "client_paths": [requests_by_label[label]["path"] for label in labels],
        "service_logged_paths": [correlations_by_label[label]["service_matches"][0]["path"] for label in labels],
        "response_body_keys": [query["info_keys"], query["stats_keys"]],
    }
    (root / "analysis.json").write_text(json.dumps(output, indent=2) + "\n")
    manifest = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in ("SHA256SUMS.json", "analysis.json", "verification.json"):
            manifest[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({
        "timing": {delay: {arm: [v["correct"], v["trials"], v["upstream_requests"]]
                           for arm, v in arms.items()} for delay, arms in output["timing"].items()},
        "statistics": {arm: [v["correct"], v["observed"], v["unavailable"]]
                       for arm, v in output["statistics"].items()},
        "followup": {key: [v["correct"], v["trials"]] for key, v in output["followup"].items()},
        "method_alert": output["method_alert"],
        "challenges": {key: [value["correct"], value["trials"]] for key, value in output["challenges"].items()},
        "counter_methods": output["counter_methods"], "load": output["load"], "identity": output["identity"],
        "package_utility": output["package_utility"], "query_audit": output["query_audit"],
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    analyze(parser.parse_args().evidence)
