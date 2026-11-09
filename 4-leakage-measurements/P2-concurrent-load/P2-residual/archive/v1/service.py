"""Owned P2 deployment wrapper. Importing it never provisions resources."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import time

import harness
import plan
import preflight

HERE = Path(__file__).resolve().parent
P1 = HERE.parent / "P1-meter"
P3 = HERE.parent / "P3-mirror"
P3_DEPLOY = HERE.parent / "P3-isolation"
spec = importlib.util.spec_from_file_location("p1_service", P1 / "service.py")
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
PAYLOAD = b"p1-meter fixture package bytes\n"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sources():
    policy = json.loads((HERE / "SOURCES-FROZEN.json").read_text())
    for name, expected in policy["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Frozen P2 source changed: {name}")
    for key, path in [("p1_service_sha256", P1 / "service.py"),
                      ("p1_gateway_sha256", P1 / "gateway.py"),
                      ("p1_dummy_sha256", P1 / "dummy.py"),
                      ("p1_upstream_sha256", P1 / "upstream.py"),
                      ("p1_analysis_sha256", P1 / "analysis.py"),
                      ("p3_mirror_sha256", P3 / "mirror.py"),
                      ("p3_manifest_sha256", P3 / "data/manifest.json"),
                      ("p3_deploy_sha256", P3_DEPLOY / "deploy.py")]:
        if sha(path) != policy[key]:
            raise RuntimeError(f"Frozen imported source changed: {key}")
    return policy


def check_gate():
    check_sources()
    allow = json.loads((HERE / "ALLOW-COLLECTION.json").read_text())
    if not all(allow.get(key) is True for key in [
            "p1_validated", "known_statistics_validated", "known_negative_cache_validated",
            "p1_causal_audit_passed", "p1_nulls_passed"]):
        raise RuntimeError("P1 validation has not authorized collection")
    if allow["p2_source_freeze_sha256"] != sha(HERE / "SOURCES-FROZEN.json"):
        raise RuntimeError("The P1 decision refers to a different P2 source freeze")
    required = {"audit-results.json", "report-metrics.json", "holdout-results.json",
                "FROZEN.json", "REPORTING-FROZEN.json", "known-channel-validation.json"}
    if not required.issubset(allow["evidence_sha256"]):
        raise RuntimeError("The P1 decision omits required evidence hashes")
    for name, expected in allow["evidence_sha256"].items():
        path = (HERE / "gate-evidence" / name).resolve()
        if (HERE / "gate-evidence").resolve() not in path.parents or sha(path) != expected:
            raise RuntimeError("P1 gate evidence changed")
    audit = json.loads((HERE / "gate-evidence/audit-results.json").read_text())
    metrics = json.loads((HERE / "gate-evidence/report-metrics.json").read_text())
    known = json.loads((HERE / "gate-evidence/known-channel-validation.json").read_text())
    if not audit["passed"] or audit["smoke"] or not metrics["null_gate_passed"] \
            or any(not known["mechanisms"][name]["passed"]
                   or not known["mechanisms"][name]["evidence"]
                   for name in ["download_statistics", "negative_cache"]):
        raise RuntimeError("The P1 audit or null evidence does not pass")
    if set(row["run"] for row in audit["runs"]) != {"open", "combined", "separate", "aa", "sham"} \
            or any(row["trials"] != 1300 or not row["passed"] for row in audit["runs"]):
        raise RuntimeError("P1 does not have complete audited main runs")
    if metrics["input_sha256"] != sha(HERE / "gate-evidence/holdout-results.json") \
            or metrics["reporting_freeze_sha256"] != sha(HERE / "gate-evidence/REPORTING-FROZEN.json"):
        raise RuntimeError("P1 reporting inputs do not match")
    names = old.run_ok(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
    if any(name.startswith("p1-m1-") for name in names):
        raise RuntimeError("P1 runtime is still active")
    if old.free_gib() < 2:
        raise RuntimeError("Less than 2 GiB memory is available")
    return allow


def replace_gateway(run_id):
    state = old.load_state(run_id)
    config = json.loads((old.ROOT / f"gateway-{run_id}.json").read_text())
    config["queue_size"] = 5
    path = HERE / "runtime-config" / f"gateway-{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    name = state["containers"]["gateway"]
    old.run_ok(["docker", "rm", "-f", name])
    old.sandbox(name, state["networks"]["alice"],
                extra=("-v", f"{HERE / 'gateway.py'}:/gateway.py:ro", "-v", f"{path}:/gateway.json:ro"),
                command=("python3", "/gateway.py"))
    for network in [state["networks"]["bob"], state["networks"]["services"]]:
        old.run_ok(["docker", "network", "connect", network, name])
    old.wait_ready(state["containers"]["alice"], name)


def body_bytes(row):
    if row["body_length"] == 0:
        body = b""
    elif row.get("body_text") is not None:
        body = row["body_text"].encode()
    else:
        body = PAYLOAD
    if hashlib.sha256(body).hexdigest() != row["body_sha256"]:
        raise RuntimeError("The profile response body cannot be reconstructed exactly")
    return body


def profile_for_actor(run_id, actor):
    state = old.load_state(run_id)
    trial = {"sender": actor, "receiver": "bob" if actor == "alice" else "alice",
             "label": 1, "trial_id": "profile", "token": secrets.token_hex(8)}
    client = harness.Client.sandbox(old, run_id, actor)
    rows, responses = [], {}
    try:
        for index, kind in enumerate(["get", "head", "cond_get", "range_get", "fresh_get",
                                      "repeated_cold", "repeated_warm", "folder_html", "stats"]):
            recipe = "repeated_get" if kind.startswith("repeated_") else kind
            job = plan.sender_jobs(state, run_id, {**trial, "recipe": recipe}, "separate")[0]
            job.update(offset_s=0, marker=plan.marker(run_id, trial, actor, f"profile-{index}"))
            client.start([job], time.time_ns() + 30_000_000)
            row = client.finish()[0]
            if row["status"] is None or row["status"] >= 500 or row["status"] == 401:
                raise RuntimeError("Response-profile preparation failed")
            rows.append(row)
            responses[kind] = {"status": row["status"], "headers": row["headers"],
                               "body_b64": base64.b64encode(body_bytes(row)).decode(),
                               "delay_s": min(1.0, row["elapsed_ms"] / 1000)}
    finally:
        client.close()
    return {"repository": state["repositories"][actor], "responses": responses,
            "captured_requests": rows, "approximation": "wire body/status and sequential delay only"}


def replace_shams(run_id):
    state = old.load_state(run_id)
    result = {}
    for actor in ("alice", "bob"):
        profile = profile_for_actor(run_id, actor)
        path = HERE / "runtime-config" / f"sham-{run_id}-{actor}.json"
        path.write_text(json.dumps(profile))
        name = state["containers"][f"dummy_{actor}"]
        old.run_ok(["docker", "rm", "-f", name])
        old.sandbox(name, state["networks"][f"dummy_{actor}"],
                    extra=("-v", f"{HERE / 'sham.py'}:/sham.py:ro", "-v", f"{path}:/profile.json:ro"),
                    command=("python3", "/sham.py", "--profile", "/profile.json"))
        old.wait_ready(state["containers"][actor], name)
        result[actor] = profile
    return result


def mirror_sandbox(name, network, extra, command):
    old.run_ok(["docker", "run", "-d", "--name", name, "--network", network,
                "--user", "20001:20001", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--memory", "128m",
                "--memory-swap", "128m", "--cpus", "0.5", "--pids-limit", "48",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", *extra,
                "python:3.12-slim", *command])


def provision_immutable(run_id):
    old.check_id(run_id)
    old.ensure_dirs()
    if old.state_path(run_id).exists():
        raise RuntimeError("The run identifier already exists")
    prefix = f"p1-{run_id}-"
    networks = {actor: prefix + f"net-{actor}" for actor in ("alice", "bob")}
    containers = {actor: prefix + actor for actor in ("alice", "bob")}
    containers.update({f"mirror_{actor}": prefix + f"mirror-{actor}" for actor in ("alice", "bob")})
    users = {actor: prefix + actor for actor in ("alice", "bob")}
    private = {name: "Aa0_" + secrets.token_urlsafe(24) for name in users.values()}
    descriptor = os.open(old.credentials_path(run_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(private, stream)
    for actor in ("alice", "bob"):
        old.docker_network(networks[actor])
        old.sandbox(containers[actor], networks[actor])
        config = {"tenant": actor, "queue_size": 5,
                  "origin_binding": {old.ip_address(containers[actor], networks[actor]): users[actor]},
                  "mirror_source_sha256": sha(P3 / "mirror.py"),
                  "manifest_sha256": sha(P3 / "data/manifest.json")}
        path = HERE / "runtime-config" / f"mirror-{run_id}-{actor}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config))
        mirror_sandbox(containers[f"mirror_{actor}"], networks[actor],
                       ("-v", f"{P3 / 'mirror.py'}:/mirror.py:ro",
                        "-v", f"{P3_DEPLOY / 'deploy.py'}:/deploy.py:ro",
                        "-v", f"{P3 / 'data'}:/catalog:ro", "-v", f"{path}:/config.json:ro"),
                       ("python3", "/deploy.py", "--mirror-source", "/mirror.py",
                        "--manifest", "/catalog/manifest.json", "--config", "/config.json",
                        "--log", "/tmp/base.jsonl", "--wire-log", "/tmp/wire.jsonl"))
        old.wait_ready(containers[actor], containers[f"mirror_{actor}"])
    state = {"run_id": run_id, "kind": "immutable", "configuration": "immutable",
             "networks": networks, "containers": containers, "users": users,
             "repositories": users.copy(), "repository_names_are": "synthetic route identifiers",
             "object_paths": {"0": "/fixture/pkg-00.bin", "15": "/fixture/pkg-15.bin"},
             "fixture_etag": '"' + hashlib.sha256(PAYLOAD).hexdigest() + '"',
             "storage_blocked": True, "negative_cache_period_seconds": 0,
             "manifest_sha256": sha(P3 / "data/manifest.json"),
             "server_tmpfs_bytes": 64 * 1024 * 1024, "server_memory_bytes": 128 * 1024 * 1024,
             "private_queue_size": 5}
    old.state_path(run_id).write_text(json.dumps(state))
    return state


def provision(run_id, configuration):
    check_gate()
    if configuration == "immutable":
        public = provision_immutable(run_id)
        public["boundary_readback"] = boundary_readback(run_id)
        public["network_positive_controls"] = preflight.network_checks(
            old, run_id, HERE / "runtime-config" / f"network-{run_id}.json")
        return public
    public = old.provision(run_id, "separate", with_dummy=configuration == "sham")
    replace_gateway(run_id)
    state = old.load_state(run_id)
    public.update(kind="mutable", scenario=configuration, fixture_etag=state["fixture_etag"],
                  gateway_edition=2, queue_size=5)
    if configuration == "sham":
        public["sham_profiles"] = replace_shams(run_id)
    public["boundary_readback"] = boundary_readback(run_id)
    public["network_positive_controls"] = preflight.network_checks(
        old, run_id, HERE / "runtime-config" / f"network-{run_id}.json")
    return public


def boundary_readback(run_id):
    state = old.load_state(run_id)
    output = {"containers": {}, "networks": {}}
    for role, name in state["containers"].items():
        item = json.loads(old.run_ok(["docker", "inspect", name]))[0]
        host, config = item["HostConfig"], item["Config"]
        forbidden = [entry.split("=", 1)[0] for entry in config.get("Env", [])
                     if entry.split("=", 1)[0].startswith(("AWS_", "BEDROCK_"))]
        row = {"name": name, "user": config["User"], "root_read_only": host["ReadonlyRootfs"],
               "cap_drop": host["CapDrop"], "security_options": host["SecurityOpt"],
               "memory": host["Memory"], "memory_swap": host["MemorySwap"],
               "nano_cpus": host["NanoCpus"], "pids_limit": host["PidsLimit"],
               "tmpfs": host["Tmpfs"], "forbidden_environment_present": bool(forbidden),
               "published_ports": item["NetworkSettings"]["Ports"],
               "attached_networks": list(item["NetworkSettings"]["Networks"]),
               "network_addresses": {name: value["IPAddress"] for name, value
                                     in item["NetworkSettings"]["Networks"].items()},
               "image_id": item["Image"],
               "bind_mount_destinations": [mount["Destination"] for mount in item["Mounts"]
                                           if mount["Type"] == "bind"],
               "mounts_read_only": all(not mount["RW"] for mount in item["Mounts"]
                                       if mount["Type"] == "bind")}
        if row["user"] != "20001:20001" or not row["root_read_only"] \
                or "ALL" not in (row["cap_drop"] or []) or forbidden or not row["mounts_read_only"] \
                or row["memory"] != 128 * 1024 * 1024 or row["memory_swap"] != row["memory"] \
                or row["nano_cpus"] != 500_000_000 or row["pids_limit"] != 48 \
                or "no-new-privileges:true" not in (row["security_options"] or []) \
                or any(row["published_ports"].values()):
            raise RuntimeError("A sandbox boundary differs from its contract")
        if role in ("alice", "bob"):
            expected = {state["networks"][role]}
            if f"dummy_{role}" in state["networks"]:
                expected.add(state["networks"][f"dummy_{role}"])
            if set(row["attached_networks"]) != expected or row["bind_mount_destinations"]:
                raise RuntimeError("A client has an unexpected network or host mount")
        output["containers"][role] = row
    for role, name in state["networks"].items():
        item = json.loads(old.run_ok(["docker", "network", "inspect", name]))[0]
        row = {"name": name, "internal": item["Internal"],
               "options": item["Options"], "containers": sorted(
                   value["Name"] for value in item["Containers"].values())}
        if not row["internal"] or row["options"].get("com.docker.network.bridge.gateway_mode_ipv4") != "isolated":
            raise RuntimeError("A network boundary differs from its contract")
        output["networks"][role] = row
    return output


def server_log_lines(run_id):
    needle = f"p1/{run_id}/"
    script = ("cd /var/opt/jfrog/artifactory/log && "
              "for f in artifactory-request.log router-request.log; do cat \"$f\"; done; "
              "for f in archived/artifactory-request.*.log.gz archived/router-request.*.log.gz; do "
              "[ -f \"$f\" ] && gzip -dc \"$f\"; done; true")
    return [line for line in old.run_ok(["docker", "exec", "art-a", "sh", "-c", script]).splitlines()
            if needle in line]


def collect(run_id):
    state = old.load_state(run_id)
    if state.get("kind") == "immutable":
        output = {"run_id": run_id, "kind": "immutable", "tenants": {}, "upstream_requests": 0}
        for actor in ("alice", "bob"):
            name = state["containers"][f"mirror_{actor}"]
            output["tenants"][actor] = {
                "response_log": [json.loads(line) for line in old.run_ok(
                    ["docker", "exec", name, "cat", "/tmp/base.jsonl"]).splitlines()],
                "wire_log": [json.loads(line) for line in old.run_ok(
                    ["docker", "exec", name, "cat", "/tmp/wire.jsonl"]).splitlines()],
                "startup": old.docker_log(name)}
        return output, []
    output = old.collect(run_id)
    output.pop("artifactory_request_log_lines", None)
    output.pop("artifactory_router_log", None)
    if "dummy_alice" in state["containers"]:
        output["dummy_logs"] = {actor: old.docker_log(state["containers"][f"dummy_{actor}"])
                                for actor in ("alice", "bob")}
    return output, server_log_lines(run_id)


def cleanup(run_id):
    # Explicit names only, including resources created before a state write.
    removed = [old.run(["docker", "rm", "-f", f"p1-{run_id}-mirror-{actor}"]).returncode
               for actor in ("alice", "bob")]
    state = old.load_state(run_id) if old.state_path(run_id).exists() else None
    output = old.cleanup(run_id)
    if state and state.get("kind") == "immutable":
        output["fixtures_left_in_place"] = {"catalog_manifest_sha256": state["manifest_sha256"],
                                           "repository_names_are": "synthetic route identifiers"}
    output["mirror_removal_codes"] = removed
    return output
