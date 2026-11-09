#!/usr/bin/env python3
"""Supplemental causal gate. No fitting and no holdout access before freezes."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
P1 = HERE.parent / "P1-meter"
TOLERANCE_NS = 2_000_000
MIN_CELL = 10


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def check_policy():
    own = load(HERE / "VALIDATION-FROZEN.json")
    for name, expected in own["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Validation source changed: {name}")
    for name, expected in own["p1_import_sha256"].items():
        if sha(P1 / name) != expected:
            raise RuntimeError(f"Imported P1 source changed: {name}")
    for name in ("FROZEN.json", "REPORTING-FROZEN.json"):
        policy = load(P1 / name)
        for relative, expected in policy["sha256"].items():
            if sha(P1 / relative) != expected:
                raise RuntimeError(f"P1 freeze drift: {relative}")


def core():
    spec = importlib.util.spec_from_file_location("validation_p1_core", P1 / "analysis.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def by_probe(trial, phase, name):
    selected = [row for row in trial[phase] if row["probe"] == name]
    if len(selected) != 1:
        raise ValueError("Missing or duplicate receiver probe")
    return selected[0]


def stats_value(record):
    if record["status"] != 200 or not isinstance(record.get("body_text"), str):
        raise ValueError("Statistics response is unavailable")
    result = json.loads(record["body_text"])
    count = result.get("downloadCount")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("Invalid download count")
    return result


def selected_confirmations(metrics, calibration, mechanism):
    expected = {
        "download_statistics": ("get", lambda name: "stats" in name
                                and ("downloadCount" in name or "lastDownloadedBy" in name)),
        "negative_cache": ("rdv_get", lambda name: name == "post.rdv_a|elapsed_ms"),
    }
    recipe, match = expected[mechanism]
    selected = {(row["feature"], row["recipe"])
                for row in calibration["open"]["selected_for_confirmation"]}
    found = []
    for index, row in enumerate(metrics["confirmations"]):
        if row["run"] == "open" and row["recipe"] in (None, recipe) \
                and match(row["feature"]) and row["p_holm_experiment"] < 0.05 \
                and (row["feature"], row["recipe"]) in selected:
            found.append({"locator": f"report-metrics.json#/confirmations/{index}",
                          "feature": row["feature"], "recipe": row["recipe"],
                          "p_holm_experiment": row["p_holm_experiment"]})
    return found


def statistics(trials, users):
    selected = [(index, trial) for index, trial in enumerate(trials)
                if trial["recipe"] == "get"]
    evidence, failures = [], []
    counts = Counter(trial["label"] for _, trial in selected)
    for index, trial in selected:
        tid, value = trial["trial_id"], trial["value"]
        try:
            before = by_probe(trial, "pre", f"pre.stats.{value:02d}")
            after = by_probe(trial, "post", f"post.stats.{value:02d}")
            first, last = stats_value(before), stats_value(after)
            observed = last["downloadCount"] - first["downloadCount"]
            sent = trial["sender_records"]
            expected = trial["label"]
            if observed != expected:
                raise ValueError(f"Download-count delta {observed} differs from {expected}")
            if expected:
                if len(sent) != 1 or sent[0]["method"] != "GET" \
                        or sent[0]["status"] != 200 \
                        or sent[0]["actor"] != trial["sender"] \
                        or last.get("lastDownloadedBy") != users[trial["sender"]] \
                        or not before["end_utc_ns"] <= sent[0]["start_utc_ns"] \
                        <= sent[0]["end_utc_ns"] <= after["start_utc_ns"]:
                    raise ValueError("The sender download does not explain the observed record")
            elif sent:
                raise ValueError("Idle trial has sender requests")
            evidence.append({"trial": tid, "label": expected, "observed_delta": observed,
                             "trial_locator": f"open/holdout.jsonl:{index + 1}",
                             "receiver_pre_marker": before["marker"],
                             "receiver_post_marker": after["marker"],
                             "sender_marker": sent[0]["marker"] if sent else None})
        except (KeyError, ValueError, TypeError) as error:
            failures.append({"trial": tid, "reason": str(error)})
    return {"passed": not failures and counts[0] >= MIN_CELL and counts[1] >= MIN_CELL,
            "recipe": "get", "counts": dict(counts), "evidence": evidence, "failures": failures}


def inside(origin, request):
    return request["start_utc_ns"] - TOLERANCE_NS <= origin["start_utc_ns"] \
        <= origin["end_utc_ns"] <= request["end_utc_ns"] + TOLERANCE_NS


def negative_cache(trials, upstream, users, normalizer):
    selected = [(index, trial) for index, trial in enumerate(trials)
                if trial["recipe"] == "rdv_get"]
    evidence, failures, body_groups = [], [], {0: set(), 1: set()}
    counts = Counter(trial["label"] for _, trial in selected)
    seen_paths = set()
    for index, trial in selected:
        tid, label = trial["trial_id"], trial["label"]
        try:
            received = by_probe(trial, "post", "post.rdv_a")
            # The owned upstream serves a repository-relative object path.
            path = "/" + urlsplit(received["path"]).path.lstrip("/").split("/", 1)[1]
            if path in seen_paths:
                raise ValueError("Rendezvous URL was reused")
            seen_paths.add(path)
            origins = [(number, row) for number, row in enumerate(upstream)
                       if row["path"] == path]
            if len(origins) != 1:
                raise ValueError(f"Expected one total upstream lookup, observed {len(origins)}")
            origin_index, origin = origins[0]
            if origin["method"] != "HEAD" or origin["status"] != 404 \
                    or origin["delay_ms"] != 150 or origin["body_bytes"] != 19:
                raise ValueError("Upstream miss differs from the fixed fixture")
            sent = trial["sender_records"]
            if received["status"] != 404 or received["method"] != "GET":
                raise ValueError("Receiver did not observe the expected missing GET")
            if label:
                if len(sent) != 1 or sent[0]["actor"] != trial["sender"] \
                        or sent[0]["method"] != "GET" or sent[0]["status"] != 404 \
                        or sent[0]["path"] != received["path"] or not inside(origin, sent[0]) \
                        or not sent[0]["end_utc_ns"] <= received["start_utc_ns"] \
                        or inside(origin, received):
                    raise ValueError("The sole lookup is not explained by the earlier sender GET")
            elif sent or not inside(origin, received):
                raise ValueError("Idle trial's lookup does not belong to the receiver")
            fields = normalizer.probe_fields(received, users[trial["receiver"]],
                                              users[trial["sender"]])
            body_groups[label].add((received["status"], fields["body_sha"]))
            evidence.append({"trial": tid, "label": label, "object_path": path,
                             "trial_locator": f"open/holdout.jsonl:{index + 1}",
                             "upstream_locator": f"open/server.json#/upstream_log/{origin_index}",
                             "lookup_owner": "sender" if label else "receiver",
                             "receiver_marker": received["marker"],
                             "sender_marker": sent[0]["marker"] if sent else None,
                             "receiver_elapsed_ms": received["elapsed_ms"]})
        except (KeyError, ValueError, TypeError, IndexError) as error:
            failures.append({"trial": tid, "reason": str(error)})
    same_bodies = body_groups[0] == body_groups[1] and len(body_groups[0]) == 1
    return {"passed": not failures and same_bodies
            and counts[0] >= MIN_CELL and counts[1] >= MIN_CELL,
            "recipe": "rdv_get", "counts": dict(counts), "normalized_receiver_body_equal": same_bodies,
            "evidence": evidence, "failures": failures}


def validate(data, results):
    check_policy()
    audit = load(data / "audit-results.json")
    metrics = load(results / "report-metrics.json")
    holdout = load(results / "holdout-results.json")
    frozen = load(P1 / "FROZEN.json")
    calibration = load(P1 / frozen["output_dir"] / "calibration-results.json")
    runs = {"open", "combined", "separate", "aa", "sham"}
    if not audit["passed"] or audit["smoke"] \
            or audit["audit_source_sha256"] != sha(P1 / "audit.py") \
            or {row["run"] for row in audit["runs"]} != runs \
            or any(not row["passed"] or row["trials"] != 1300 for row in audit["runs"]) \
            or set(holdout) != runs or any(row["trials"] != 1000 for row in holdout.values()) \
            or metrics["input_sha256"] != sha(results / "holdout-results.json") \
            or metrics["reporting_freeze_sha256"] != sha(P1 / "REPORTING-FROZEN.json"):
        raise RuntimeError("Complete unchanged main evidence is required")
    inputs = {}
    for run in sorted(runs):
        folder = data / run
        seal = load(folder / "sealed.json")
        for split, count in (("calibration", 300), ("holdout", 1000)):
            file = folder / f"{split}.jsonl"
            if sha(file) != seal["files"][split]["sha256"] \
                    or count != seal["files"][split]["trials"] or len(rows(file)) != count:
                raise RuntimeError("A sealed P1 split differs")
        inputs.update({f"{run}/{name}": sha(folder / name) for name in
                       ("sealed.json", "calibration.jsonl", "holdout.jsonl", "provision.json",
                        "server.json", "server-log-lines.txt", "cleanup.json")})
        if sha(folder / "calibration.jsonl") != calibration[run]["calibration_sha256"] \
                or sha(folder / "provision.json") != calibration[run]["provision_sha256"]:
            raise RuntimeError("Calibration provenance differs")
    trials = rows(data / "open/holdout.jsonl")
    users = load(data / "open/provision.json")["users"]
    mechanisms = {
        "download_statistics": statistics(trials, users),
        "negative_cache": negative_cache(trials, load(data / "open/server.json")["upstream_log"],
                                          users, core()),
    }
    for name, row in mechanisms.items():
        confirmed = selected_confirmations(metrics, calibration, name)
        row["frozen_meter_confirmations"] = confirmed
        row["passed"] = row["passed"] and bool(confirmed)
    return {"passed": all(row["passed"] for row in mechanisms.values()),
            "utc": datetime.now(timezone.utc).isoformat(), "mechanisms": mechanisms,
            "validation_freeze_sha256": sha(HERE / "VALIDATION-FROZEN.json"),
            "audit_results_sha256": sha(data / "audit-results.json"),
            "report_metrics_sha256": sha(results / "report-metrics.json"),
            "input_sha256": inputs, "null_gate_passed": metrics["null_gate_passed"],
            "scope": "Known causal mechanisms and prior frozen meter confirmations; no new decoder."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    output = validate(args.data, args.results)
    path = args.results / "known-channel-validation.json"
    with path.open("x") as stream:
        json.dump(output, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"passed": output["passed"], "null_gate_passed": output["null_gate_passed"],
                      "mechanisms": {name: {"passed": row["passed"], "counts": row["counts"],
                                          "failures": row["failures"]}
                                     for name, row in output["mechanisms"].items()}}, indent=2))
    if not output["passed"]:
        raise SystemExit(1)
