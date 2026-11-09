#!/usr/bin/env python3
"""Retain only H's public fixture evidence, never private credential files."""
from collections import Counter
import json
from pathlib import Path
import subprocess
from probe import CREDS, OUT, redact, req, require


def command(*args):
    return subprocess.check_output(args, text=True)


def main():
    for filename in ("probe-state.json", "remote-state.json"):
        state = json.loads((OUT / filename).read_text())
        require(req("final-config-" + filename, "GET",
                    "/ui/admin/repositories/" + ("local/" if filename == "probe-state.json" else "remote/") + state["repo"]))
    states = [json.loads((OUT / name).read_text()) for name in ("probe-state.json", "remote-state.json")]
    for target in [states[0]["run"], states[0]["run"] + "-h-sender",
                   states[0]["run"] + "-h-receiver", states[1]["run"] + "-remote-perm"]:
        require(req("final-permission-" + target, "GET", "/ui/permissiontargets/" + target))
    logs = OUT / "logs"
    logs.mkdir(exist_ok=True)
    requestlog = command("docker", "exec", "art-a", "cat", "/var/opt/jfrog/artifactory/log/artifactory-request.log")
    selected = [line for line in requestlog.splitlines() if "|h-infra/" in line]
    (logs / "artifactory-request.log").write_text(redact("\n".join(selected) + "\n"))
    traces = {line.split("|")[1] for line in selected}
    for name in ("artifactory-access.log", "artifactory-request-out.log"):
        text = command("docker", "exec", "art-a", "cat", "/var/opt/jfrog/artifactory/log/" + name)
        own = [line for line in text.splitlines()
               if any(t in line for t in traces) or "h-upstream:" in line or "h-target:" in line
               or any(s["repo"] in line for s in states)]
        (logs / name).write_text(redact("\n".join(own) + "\n"))
    text = command("docker", "exec", "art-a", "cat", "/var/opt/jfrog/artifactory/log/router-request.log")
    router = []
    safe = ("TraceId", "StartUTC", "Duration", "RequestMethod", "RequestPath",
            "ClientAddr", "DownstreamStatus", "DownstreamContentSize", "request_User-Agent")
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("request_User-Agent", "").startswith("h-infra/"):
            router.append({k: d[k] for k in safe if k in d})
    (logs / "router-request.jsonl").write_text("".join(json.dumps(d) + "\n" for d in router))
    for container in ("h-upstream", "h-target"):
        (logs / (container + ".jsonl")).write_text(command("docker", "logs", container))
    images = {}
    for image in ("python:3.12-slim", "releases-docker.jfrog.io/jfrog/artifactory-oss:7.161.26"):
        d = json.loads(command("docker", "image", "inspect", image))[0]
        images[image] = {"Id": d["Id"], "RepoDigests": d["RepoDigests"]}
    (OUT / "image-identities.json").write_text(json.dumps(images, indent=2))
    network = []
    for name in ("h-net-sender", "h-net-receiver", "h-net-services"):
        d = json.loads(command("docker", "network", "inspect", name))[0]
        network.append({k: d[k] for k in ("Name", "Internal", "Options", "IPAM", "Containers")})
    (OUT / "networks.json").write_text(json.dumps(network, indent=2))
    inventory = []
    for container in ("h-sender", "h-receiver", "h-upstream", "h-target"):
        d = json.loads(command("docker", "inspect", container))[0]
        h = d["HostConfig"]
        inventory.append({"name": container, "image": d["Image"], "user": d["Config"]["User"],
                          "settings": {k: h[k] for k in ("Privileged", "ReadonlyRootfs", "CapDrop",
                                      "SecurityOpt", "Memory", "MemorySwap", "PidsLimit", "Binds")},
                          "networks": d["NetworkSettings"]["Networks"]})
    (OUT / "containers.json").write_text(json.dumps(inventory, indent=2))
    records = [json.loads(line) for line in (OUT / "requests.jsonl").read_text().splitlines()]
    matches = {}
    for line in selected:
        p = line.split("|")
        matches.setdefault(p[10], []).append({"trace": p[1], "identity": p[3],
                                             "method": p[4], "path": p[5], "status": int(p[6])})
    correlations = []
    for r in records:
        correlations.append({"run": r["run"], "id": r["id"], "user_agent": r["user_agent"],
                             "matches": matches.get(r["user_agent"], [])})
    (OUT / "log-matches.json").write_text(json.dumps(correlations, indent=2))
    (OUT / "collection-summary.json").write_text(json.dumps({
        "recorded_requests": len(records), "matched_requests": sum(bool(x["matches"]) for x in correlations),
        "request_log_lines": len(selected), "router_log_rows": len(router),
        "duplicate_user_agents": sum(v > 1 for v in Counter(r["user_agent"] for r in records).values()),
    }, indent=2))
    print((OUT / "collection-summary.json").read_text())


if __name__ == "__main__":
    main()
