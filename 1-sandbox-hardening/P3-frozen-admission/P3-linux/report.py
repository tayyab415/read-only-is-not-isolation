"""Report the complete, audited matched utility workload."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(successes, total):
    z = 1.959963984540054
    center = (successes / total + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(successes / total * (1 - successes / total) / total
                         + z * z / (4 * total * total)) / (1 + z * z / total)
    return [center - half, center + half]


def summarize(data):
    audit = json.loads((data / "audit-results.json").read_text())
    if not audit["passed"]:
        raise RuntimeError("Utility evidence has not passed its independent audit")
    freeze = json.loads((HERE / "UTILITY-FROZEN.json").read_text())
    if audit["audit_source_sha256"] != freeze["sha256"]["audit.py"] \
            or set(audit["sealed_sha256"]) != set(freeze["arms"]) \
            or any(sha(data / mode / "sealed.json") != expected
                   for mode, expected in audit["sealed_sha256"].items()):
        raise RuntimeError("The audit refers to different source or evidence")
    for name, expected in freeze["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError("Frozen utility reporting source changed")
    arms, paired = {}, {}
    for mode in freeze["arms"]:
        folder = data / mode
        seal = json.loads((folder / "sealed.json").read_text())
        if not seal["complete"] or any(sha(folder / name) != expected for name, expected in seal["sha256"].items()):
            raise RuntimeError("Audited utility evidence changed")
        tasks = [json.loads(line) for line in (folder / "tasks.jsonl").read_text().splitlines()]
        server = json.loads((folder / "server.json").read_text())
        prepared = json.loads((folder / "prepared.json").read_text())
        success = sum(row["passed"] for row in tasks)
        events = [row for rows in server["adapter_logs"].values() for row in rows]
        first_task = min(row["start_utc_ns"] for row in tasks)
        upstream = server.get("package_upstream_wire_log", [])
        arms[mode] = {
            "scheduled": 28, "completed": len(tasks), "passed": success, "success_fraction": success / 28,
            "wilson95_task_interval": wilson(success, 28),
            "elapsed_s_median": statistics.median(row["elapsed_s"] for row in tasks),
            "elapsed_s_range": [min(row["elapsed_s"] for row in tasks), max(row["elapsed_s"] for row in tasks)],
            "index_and_wheel_requests": len(events),
            "backend_wheel_requests": sum(row["backend_target"] is not None for row in events),
            "wheel_payload_bytes": sum(row["body_bytes"] for row in events if row["backend_target"] is not None),
            "all_client_response_bytes": sum(row["body_bytes"] for row in events),
            "trusted_package_fill_requests": len(prepared.get("trusted_package_prewarm", [])),
            "trusted_package_fill_bytes": sum(row["body_bytes"] for row in prepared.get("trusted_package_prewarm", [])),
            "trusted_source_requests_during_setup": sum(row["end_utc_ns"] < first_task for row in upstream),
            "trusted_source_requests_during_tasks": sum(row["end_utc_ns"] >= first_task for row in upstream),
            "external_package_requests_during_tasks": 0,
            "client_image_ids": sorted({prepared["boundary_readback"]["containers"][actor]["image_id"]
                                        for actor in ("alice", "bob")}),
            "python_versions": sorted({row["python"] for row in tasks}),
            "pip_versions": sorted({row["pip_version"] for row in tasks}),
            "memory_observations": {key: prepared[key] for key in ("memory_before", "memory_after")},
            "seal_sha256": sha(folder / "sealed.json"),
            "cleanup_absence_proved": seal["cleanup_absence_proved"],
        }
        for row in tasks:
            paired.setdefault(row["task_id"], {})[mode] = row
    pairs = [{"task_id": task, "requirement": rows["separate"]["requirement"],
              "separate_passed": rows["separate"]["passed"], "immutable_passed": rows["immutable"]["passed"],
              "separate_s": rows["separate"]["elapsed_s"], "immutable_s": rows["immutable"]["elapsed_s"],
              "immutable_minus_separate_s": rows["immutable"]["elapsed_s"] - rows["separate"]["elapsed_s"]}
             for task, rows in paired.items()]
    clusters = {}
    for row in pairs:
        clusters.setdefault(row["requirement"], []).append(row["immutable_minus_separate_s"])
    rng = random.Random(2026110809)
    cluster_values = list(clusters.values())
    boot = sorted(statistics.median(value for cluster in rng.choices(cluster_values, k=len(cluster_values))
                                   for value in cluster) for _ in range(10000))
    manifest = json.loads((HERE.parent / "P3-mirror/data/manifest.json").read_text())
    return {
        "source_freeze_sha256": sha(HERE / "UTILITY-FROZEN.json"),
        "audit_sha256": sha(data / "audit-results.json"),
        "arms_in_run_order": freeze["arms"], "arms": arms, "paired_tasks": pairs,
        "matched_latency_median_difference_s": statistics.median(row["immutable_minus_separate_s"] for row in pairs),
        "root_cluster_bootstrap95_median_difference_s": [boot[249], boot[9749]],
        "admitted_unique_wheels": len(manifest["entries"]), "admitted_unique_bytes": manifest["total_bytes"],
        "logical_in_memory_catalog_copies_per_arm": {"separate": 3, "immutable": 4},
        "catalog_copy_accounting": "Two common adapters in both arms; one trusted admission source in separate, two private serving processes in immutable. Shared Artifactory and its stored per-repository copies are additional.",
        "limitations": [
            "Cold private install directories against prewarmed service content.",
            "Fourteen fixed universal-wheel roots on one Linux host, two synthetic tenants, one install per root per tenant per arm.",
            "Latency intervals resample fourteen root clusters; they do not cover independent host deployments.",
            "Task success intervals describe the 28 scheduled tasks and assume independent Bernoulli outcomes for the interval calculation; dependencies can correlate outcomes.",
            "Both arms add the common private index adapter; this is not a native PyPI registry benchmark.",
            "Docker memory observations are snapshots, not peak memory or process RSS.",
            "The utility workload establishes no timing-isolation or unrestricted package-coverage claim.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    args = parser.parse_args()
    result = summarize(args.data)
    (args.data / "utility-report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ("paired_tasks", "arms")}, indent=2))


if __name__ == "__main__":
    main()
