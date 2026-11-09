#!/usr/bin/env python3
"""Check the recorded experiment against the frozen schedule and raw evidence."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def verify(root):
    checks = []

    def check(name, condition, detail=None):
        checks.append({"name": name, "passed": bool(condition), "detail": detail})

    plan = json.loads((root / "preregister.json").read_text())
    timing = read_rows(root / "timing-trials.jsonl")
    calibration = read_rows(root / "timing-calibration.jsonl")
    stat = read_rows(root / "stat-trials.jsonl")
    request_rows = read_rows(root / "requests.jsonl")
    request_map = {r["label"]: r for r in request_rows}
    thresholds = json.loads((root / "frozen-thresholds.json").read_text())
    check("terminal success record", json.loads((root / "run-finished.json").read_text())["status"] == "complete")
    check("unique request labels", len(request_map) == len(request_rows))
    check("unique timing paths", len(set(r["relative_path"] for r in timing + calibration)) == len(timing + calibration))
    check("full timing schedule", len(timing) == 544, {"actual": len(timing), "expected": 544})
    check("full calibration schedule", len(calibration) == 24)
    check("full statistics schedule", len(stat) == 168)
    for delay in (150, 20):
        label = str(delay)
        data = [r for r in calibration if r["delay_ms"] == delay]
        check(f"calibration bits {delay}", [r["expected_bit"] for r in data] == plan["calibration_bits"][label])
        first_holdout = min(request_map[r["receiver_label"]]["start_utc_ns"] for r in timing
                            if r["phase"] == "holdout")
        check(f"threshold frozen before holdout {delay}", thresholds[label]["frozen_utc_ns"] < first_holdout)
        for arm, expected in plan["timing_bits"][label].items():
            data = [r for r in timing if r["phase"] == "holdout" and r["delay_ms"] == delay and r["arm"] == arm]
            check(f"holdout schedule {delay} {arm}", [r["expected_bit"] for r in data] == expected)
        data = [r for r in timing if r["phase"] == "no-sender" and r["delay_ms"] == delay]
        check(f"no-sender schedule {delay}", len(data) == 16 and all(
            r["expected_bit"] == 0 and r["sender_label"] is None for r in data))
    for arm, expected in plan["statistics_bits"].items():
        data = [r for r in stat if r["arm"] == arm]
        check(f"statistics schedule {arm}", [r["expected_bit"] for r in data] == expected)
    check("repeat schedule", [r["expected_bit"] for r in timing if r["phase"] == "repeat"] == plan["repeat_bits"])
    for arm in ("method-only", "both"):
        check("HEAD schedule " + arm, [r["expected_bit"] for r in timing
                                      if r["phase"] == "head" and r["arm"] == arm] == plan["head_bits"])
    check("decoder frozen in every timing row", all(
        r["decoded_bit"] == int(r["duration_ns"] < thresholds[str(r["delay_ms"])]["threshold_ns"])
        for r in timing))
    check("timing duration from receiver record", all(
        r["duration_ns"] == request_map[r["receiver_label"]]["duration_ns"] for r in timing + calibration))
    check("timing statuses are retained 404s", all(
        r["status"] == request_map[r["receiver_label"]]["status"] == 404 for r in timing + calibration))
    check("sender requests match scheduled bits", all(
        bool(r["sender_label"]) == bool(r["expected_bit"]) for r in timing + calibration
        if r["phase"] != "denied-prime"))
    check("calibration identities distinct", all(
        request_map[r["sender_label"]]["identity"] != request_map[r["receiver_label"]]["identity"]
        for r in timing + calibration if r["sender_label"]))
    check("response bodies agree with retained hashes", all(
        hashlib.sha256((root / r["body_file"]).read_bytes()).hexdigest() == r["body_sha256"]
        for r in request_rows))
    stats_ok = True
    for row in stat:
        before, after = request_map[row["before_label"]], request_map[row["after_label"]]
        counts = []
        for record in (before, after):
            body = (root / record["body_file"]).read_text()
            counts.append(json.loads(body).get("downloadCount") if record["status"] == 200 else None)
        decoded = int(counts[1] > counts[0]) if None not in counts else None
        stats_ok &= row["before_count"] == counts[0] and row["after_count"] == counts[1] and row["decoded_bit"] == decoded
    check("statistics recomputed from response bodies", stats_ok)
    collection = json.loads((root / "collection-summary.json").read_text())
    check("all forwarded requests matched exactly once", collection["expected_service_requests"] ==
          collection["exactly_one_service_match"])
    check("all client requests matched at gateway exactly once", collection["gateway_requests"] ==
          collection["exactly_one_gateway_match"])
    check("gateway blocks did not reach service", not collection["service_matches_for_gateway_blocks"])
    correlations = json.loads((root / "log-matches.json").read_text())
    canonical_names = {}
    user_snapshots = []
    for role in ("sender", "receiver", "prefetch"):
        record = request_map["read-user-" + role]
        snapshot = json.loads((root / record["body_file"]).read_text())
        canonical_names[record["path"].rsplit("/", 1)[-1]] = snapshot["name"]
        user_snapshots.append(snapshot)
    check("test users are non-admin without groups", all(
        snapshot["admin"] is False and snapshot["numberOfGroups"] == 0
        for snapshot in user_snapshots))
    check("service matched authenticated identity for non-401 responses", all(
        match["identity"] == canonical_names.get(
            request_map[row["label"]]["identity"], request_map[row["label"]]["identity"])
        for row in correlations for match in row["service_matches"] if match["status"] != 401))
    check("service matched method and status", all(
        match["method"] == row["method"] and match["status"] == row["status"]
        for row in correlations for match in row["service_matches"]))
    utility = json.loads((root / "utility.json").read_text())
    warmed = [r for r in utility if r["check"] == "prewarmed"]
    check("ordinary bytes preserved in all arms", len(warmed) == 14 and
          all(r["status"] == 200 for r in warmed) and len(set(r["sha256"] for r in warmed)) == 1)
    connectivity = json.loads((root / "connectivity.json").read_text())
    check("clients reach only gateway among tested targets", all(
        row["checks"][0]["result"] == "connected" and
        all(r["result"] != "connected" for r in row["checks"][1:]) for row in connectivity))
    metadata = json.loads((root / "image-identities.json").read_text())
    art_image = metadata["releases-docker.jfrog.io/jfrog/artifactory-oss:7.161.26"]
    check("Artifactory digest pinned", art_image["Id"] ==
          "sha256:d103c0464d33645acdcbfbb6fc61310d7faf14c9893975cbee562487eaeac1d1")
    manifest = json.loads((root / "SHA256SUMS.json").read_text())
    check("all evidence manifest hashes match", all(
        hashlib.sha256((root / path).read_bytes()).hexdigest() == digest for path, digest in manifest.items()))
    followup_plan = json.loads((root / "followup-preregister.json").read_text())
    followup = read_rows(root / "followup-timing.jsonl")
    check("followup terminal success", json.loads((root / "followup-finished.json").read_text())["status"] == "complete")
    check("followup timing trial count", len(followup) == 80)
    for arm, expected in followup_plan["load_bits"].items():
        data = [r for r in followup if r["phase"] == "load" and r["arm"] == arm]
        check("load schedule " + arm, [r["expected_bit"] for r in data] == expected)
    check("denied prime schedule", [r["expected_bit"] for r in followup
                                    if r["phase"] == "denied-prime"] == followup_plan["denied_prime_bits"])
    check("followup decoder unchanged", all(
        r["decoded_bit"] == int(r["duration_ns"] < thresholds[str(r["delay_ms"])]["threshold_ns"])
        for r in followup))
    check("unauthorized primers were denied", all(
        r["sender_status"] in (401, 403) for r in followup
        if r["phase"] == "denied-prime" and r["sender_label"]))
    noise = [r for r in request_rows if r["label"].startswith("noise-") and r["label"] != "noise-prewarm"]
    check("all load requests retained and succeeded", len(noise) == 320 and all(r["status"] == 200 for r in noise))
    counters = read_rows(root / "counter-followup.jsonl")
    check("counter followup count", len(counters) == 48)
    for mode, expected in followup_plan["counter_bits"].items():
        check("counter method schedule " + mode, [r["expected_bit"] for r in counters
                                                  if r["mode"] == mode] == expected)
    counter_ok = True
    for row in counters:
        counts = [json.loads((root / request_map[row[key]]["body_file"]).read_text())["downloadCount"]
                  for key in ("before_label", "after_label")]
        counter_ok &= counts == [row["before_count"], row["after_count"]] and row["decoded_bit"] == int(counts[1] > counts[0])
    check("counter methods recomputed from retained bodies", counter_ok)
    identities = read_rows(root / "identity-trials.jsonl")
    identity_plan = json.loads((root / "identity-preregister.json").read_text())
    check("identity terminal success", json.loads((root / "identity-finished.json").read_text())["status"] == "complete")
    check("identity trial count", len(identities) == 32)
    for arm in identity_plan["arms"]:
        data = [r for r in identities if r["arm"] == arm]
        check("identity schedule " + arm, [r["client_role"] for r in data] == identity_plan["roles"])
        check("own downloads preserved " + arm, all(r["own_status"] == 200 for r in data))
    check("identical ordinary bytes after identity binding", len(set(r["own_body_sha256"] for r in identities)) == 1)
    check("valid copied credentials work through account-only gateway", all(
        r["borrowed_status"] == 200 for r in identities if r["arm"] == "account-only"))
    check("copied credentials rejected by bound gateway", all(
        r["borrowed_status"] == 403 for r in identities if r["arm"] == "origin-bound"))
    negative_controls = read_rows(root / "identity-negative-controls.jsonl")
    check("wrong passwords rejected in both identity configurations", len(negative_controls) == 4 and
          all(r["status"] == 401 for r in negative_controls))
    package = read_rows(root / "package-trials.jsonl")
    package_plan = json.loads((root / "package-preregister.json").read_text())
    check("package utility terminal success", json.loads((root / "package-finished.json").read_text())["status"] == "complete")
    check("all package task configurations retained", len(package) == 14 and
          set((r["arm"], r["role"]) for r in package) ==
          set((arm, role) for arm in package_plan["arms"] for role in ("sender", "receiver")))
    check("all package installations and functions succeeded", all(
        r["download_status"] == 200 and r["pip_exit_code"] == 0 and
        r["installed_version"] == "1.16.0" and r["package_function_passed"] is True for r in package))
    check("all downloaded wheels match pinned PyPI hash", all(
        r["download_sha256"] == package_plan["expected_sha256"] ==
        request_map[r["label"]]["body_sha256"] for r in package))
    result = {"checks": len(checks), "passed": sum(c["passed"] for c in checks),
              "failed": [c for c in checks if not c["passed"]], "details": checks,
              "scope": "Evidence integrity and protocol compliance, not universal channel absence."}
    (root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "details"}, indent=2))
    return not result["failed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    raise SystemExit(0 if verify(parser.parse_args().evidence) else 1)
