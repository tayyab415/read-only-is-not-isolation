#!/usr/bin/env python3
"""Collect owned fixtures and correlate independent service logs."""
from collections import Counter
import argparse
import json
from pathlib import Path
import subprocess
import time
from campaign import ROOT, STATE, Requests, append, redact, save


def command(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout


def main(refresh_only=False):
    state = json.loads(STATE.read_text())
    q = Requests(state)
    if not refresh_only:
        for repo in state["configs"]:
            _, text = q.required("final-config-" + repo, "GET", "/ui/admin/repositories/remote/" + repo)
            save(q.out / ("final-config-" + repo + ".json"), json.loads(text))
            _, text = q.required("final-grants-" + repo, "GET", "/ui/permissiontargets/" + repo + "-perm")
            save(q.out / ("final-grants-" + repo + ".json"), json.loads(text))
    log_dir = q.out / "logs"
    log_dir.mkdir(exist_ok=True)
    selected = []
    prefix = f'j-infra/{state["run"]}/'
    # Current logs should cover this short run. Missing matches remain explicit.
    text = command("docker", "exec", "art-a", "cat",
                   "/var/opt/jfrog/artifactory/log/artifactory-request.log")
    for line in text.splitlines():
        if prefix in line:
            selected.append(line)
    (log_dir / "artifactory-request.log").write_text(redact("\n".join(selected) + "\n", q.creds))
    traces = {line.split("|")[1] for line in selected}
    for name in ("artifactory-access.log", "artifactory-request-out.log"):
        text = command("docker", "exec", "art-a", "cat", "/var/opt/jfrog/artifactory/log/" + name)
        own = [line for line in text.splitlines() if "j-upstream:" in line or
               state["run"] in line or any(trace in line for trace in traces)]
        (log_dir / name).write_text(redact("\n".join(own) + "\n", q.creds))
    for container in ("j-upstream", "j-gateway"):
        (log_dir / (container + ".jsonl")).write_text(command("docker", "logs", container))
    matches = {}
    for line in selected:
        pieces = line.split("|")
        if len(pieces) >= 11:
            matches.setdefault(pieces[10], []).append({
                "timestamp": pieces[0], "trace": pieces[1], "identity": pieces[3],
                "method": pieces[4], "path": pieces[5], "status": int(pieces[6]),
                "request_bytes": pieces[7], "response_bytes": pieces[8],
                "duration_ms": pieces[9],
            })
    gate = [json.loads(line) for line in (log_dir / "j-gateway.jsonl").read_text().splitlines()]
    gate_matches = {}
    for row in gate:
        gate_matches.setdefault(row["marker"], []).append(row)
    records = [json.loads(line) for line in (q.out / "requests.jsonl").read_text().splitlines()]
    correlations = []
    for row in records:
        correlations.append({
            "label": row["label"], "marker": row["marker"], "transport": row["transport"],
            "method": row["method"], "status": row["status"],
            "service_matches": matches.get(row["marker"], []),
            "gateway_matches": gate_matches.get(row["marker"], []),
        })
    save(q.out / "log-matches.json", correlations)
    inventory = []
    for container in ("j-sender", "j-receiver", "j-gateway", "j-upstream"):
        data = json.loads(command("docker", "inspect", container))[0]
        host = data["HostConfig"]
        inventory.append({
            "name": container, "image": data["Image"], "user": data["Config"]["User"],
            "settings": {key: host[key] for key in
                         ("Privileged", "ReadonlyRootfs", "CapDrop", "SecurityOpt",
                          "Memory", "MemorySwap", "PidsLimit", "Binds")},
            "networks": data["NetworkSettings"]["Networks"],
        })
    save(q.out / "containers.json", inventory)
    networks = []
    for name in ("j-net-sender", "j-net-receiver", "j-net-services"):
        data = json.loads(command("docker", "network", "inspect", name))[0]
        networks.append({key: data[key] for key in ("Name", "Internal", "Options", "IPAM", "Containers")})
    save(q.out / "networks.json", networks)
    images = {}
    for name in ("python:3.12-slim", "releases-docker.jfrog.io/jfrog/artifactory-oss:7.161.26"):
        data = json.loads(command("docker", "image", "inspect", name))[0]
        images[name] = {"Id": data["Id"], "RepoDigests": data["RepoDigests"]}
    save(q.out / "image-identities.json", images)
    expected_service = []
    unexpected = []
    for row, correlation in zip(records, correlations):
        blocked = any(r["policy_reason"] for r in correlation["gateway_matches"])
        if not blocked:
            expected_service.append(correlation)
        elif correlation["service_matches"]:
            unexpected.append(row["label"])
    summary = {
        "utc_ns": time.time_ns(), "recorded_requests": len(records),
        "unique_markers": len(set(r["marker"] for r in records)),
        "expected_service_requests": len(expected_service),
        "exactly_one_service_match": sum(len(r["service_matches"]) == 1 for r in expected_service),
        "gateway_requests": sum(r["transport"].startswith("j-") for r in records),
        "exactly_one_gateway_match": sum(len(r["gateway_matches"]) == 1 for r in correlations
                                        if r["transport"].startswith("j-")),
        "service_matches_for_gateway_blocks": unexpected,
        "unmatched_service_labels": [r["label"] for r in expected_service if not r["service_matches"]],
        "duplicated_markers": [key for key, value in Counter(r["marker"] for r in records).items() if value > 1],
        "collection_scope": "Only this campaign's labelled requests; not a claim about untagged traffic.",
    }
    save(q.out / "collection-summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-only", action="store_true")
    main(parser.parse_args().refresh_only)
