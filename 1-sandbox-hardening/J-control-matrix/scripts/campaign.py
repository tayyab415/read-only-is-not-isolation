#!/usr/bin/env python3
"""Provision and run an owned infrastructure experiment on the existing lab host."""
import argparse
import base64
import copy
import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import secrets
import statistics
import subprocess
import time

ROOT = Path.home() / "workspaces/artifactory-lab/j"
PRIVATE = ROOT / "credentials.json"
STATE = ROOT / "state.json"
ARMS = (
    "method-only", "metadata-blocked", "negative-off", "both",
    "partitioned", "no-store", "offline",
)
PAYLOAD = b"J controls harmless artifact fixture v1\n"
CLIENT_CODE = r"""
import base64, http.client, json, sys, time
d = json.load(sys.stdin)
headers = {
    "Authorization": "Basic " + base64.b64encode(
        (d["username"] + ":" + d["password"]).encode()).decode(),
    "User-Agent": d["marker"],
}
headers.update(d.get("headers", {}))
c = http.client.HTTPConnection("j-gateway", 8000, timeout=35)
t0, m0 = time.time_ns(), time.monotonic_ns()
try:
    c.request(d["method"], "/artifactory" + d["path"], headers=headers)
    r = c.getresponse()
    body = r.read()
    print(json.dumps({
        "status": r.status, "body_b64": base64.b64encode(body).decode(),
        "headers": dict(r.getheaders()), "start_utc_ns": t0,
        "end_utc_ns": time.time_ns(), "duration_ns": time.monotonic_ns() - m0,
    }))
finally:
    c.close()
"""


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def append(path, data):
    with path.open("a") as f:
        f.write(json.dumps(data) + "\n")


def read_private():
    return json.loads(PRIVATE.read_text())


def credentials():
    result = read_private()
    admin_path = Path.home() / "workspaces/artifactory-lab/a/credentials.json"
    result["admin"] = json.loads(admin_path.read_text())["admin"]
    return result


def redact(value, creds):
    for secret in creds.values():
        value = value.replace(secret, "<secret-redacted>")
    return value


class Requests:
    def __init__(self, state):
        self.state = state
        self.out = ROOT / "evidence" / state["run"]
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "bodies").mkdir(exist_ok=True)
        self.creds = credentials()

    def request(self, label, method, path, who="admin", body=None,
                direct=False, headers=None):
        username = who if who == "admin" else self.state["users"][who]
        marker = f'j-infra/{self.state["run"]}/{label}'
        encoded = json.dumps(body).encode() if isinstance(body, dict) else body
        if isinstance(encoded, str):
            encoded = encoded.encode()
        started, monotonic = time.time_ns(), time.monotonic_ns()
        if who == "admin" or direct:
            auth = base64.b64encode((username + ":" + self.creds[who]).encode()).decode()
            request_headers = {"Authorization": "Basic " + auth, "User-Agent": marker}
            if isinstance(body, dict):
                request_headers["Content-Type"] = "application/json"
            request_headers.update(headers or {})
            conn = http.client.HTTPConnection("127.0.0.1", 18584, timeout=35)
            try:
                conn.request(method, "/artifactory" + path, body=encoded, headers=request_headers)
                response = conn.getresponse()
                payload = response.read()
                data = {
                    "status": response.status, "headers": dict(response.getheaders()),
                    "start_utc_ns": started, "end_utc_ns": time.time_ns(),
                    "duration_ns": time.monotonic_ns() - monotonic,
                }
            finally:
                conn.close()
            transport = "host-direct"
        else:
            container = "j-receiver" if who == "receiver" else "j-sender"
            private_input = {
                "username": username, "password": self.creds[who], "marker": marker,
                "method": method, "path": path, "headers": headers or {},
            }
            process = subprocess.run(
                ["docker", "exec", "-i", container, "python3", "-c", CLIENT_CODE],
                input=json.dumps(private_input), text=True, capture_output=True, timeout=45,
            )
            if process.returncode:
                raise RuntimeError(redact(process.stderr[-600:], self.creds))
            data = json.loads(process.stdout)
            payload = base64.b64decode(data.pop("body_b64"))
            transport = container
        text = redact(payload.decode(errors="replace"), self.creds)
        try:
            payload.decode("utf-8")
            body_path = f"bodies/{label}.txt"
            (self.out / body_path).write_text(text)
        except UnicodeDecodeError:
            if any(secret.encode() in payload for secret in self.creds.values()):
                raise RuntimeError("A binary response contains a credential; stop before retaining it")
            body_path = f"bodies/{label}.bin"
            (self.out / body_path).write_bytes(payload)
        record = {
            "label": label, "run": self.state["run"], "identity": username,
            "role": who, "transport": transport, "method": method, "path": path,
            "marker": marker, **data, "body_file": body_path,
            "body_sha256": hashlib.sha256(payload).hexdigest(),
            "body_bytes": len(payload),
            "request_body": redact(encoded.decode(errors="replace"), self.creds) if encoded else None,
            "request_headers_without_auth": headers or {},
        }
        append(self.out / "requests.jsonl", record)
        return record, text

    def required(self, *args, **kwargs):
        record, text = self.request(*args, **kwargs)
        if not 200 <= record["status"] < 300:
            raise RuntimeError(f'{record["label"]}: HTTP {record["status"]}: {text[:300]}')
        return record, text


def setup():
    if STATE.exists():
        raise RuntimeError("A campaign already exists. Do not overwrite or reset its state.")
    ROOT.mkdir(parents=True, exist_ok=True)
    run = "j-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    user_map = {role: run + "-" + suffix for role, suffix in
                (("sender", "tx"), ("receiver", "rx"), ("prefetch", "fetch"))}
    fd = os.open(PRIVATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump({role: secrets.token_urlsafe(24) for role in user_map}, file)
    arm_repos = {}
    for arm in ARMS:
        arm_repos[arm] = {
            "sender": run + "-" + arm + ("-tx" if arm == "partitioned" else ""),
            "receiver": run + "-" + arm + ("-rx" if arm == "partitioned" else ""),
        }
    state = {"run": run, "users": user_map, "repos": arm_repos,
             "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    save(STATE, state)
    q = Requests(state)
    rng = random.Random(2026110801)

    def bits(n):
        values = [0] * (n // 2) + [1] * (n // 2)
        rng.shuffle(values)
        return values

    timing_order = {}
    timing_bits = {}
    for delay in (150, 20):
        order = list(ARMS)
        rng.shuffle(order)
        timing_order[str(delay)] = order
        timing_bits[str(delay)] = {arm: bits(32) for arm in ARMS}
    plan = {
        "seed": 2026110801, "timing_order": timing_order,
        "calibration_bits": {str(delay): bits(12) for delay in (150, 20)},
        "timing_bits": timing_bits, "statistics_bits": {arm: bits(24) for arm in ARMS},
        "no_sender_trials_per_delay": 16, "repeat_bits": bits(32), "head_bits": bits(16),
        "decoder": "1 iff receiver duration below midpoint of calibration class medians",
        "threshold_freeze": "separate calibration only; never update with holdout data",
        "budgets": {"initial_usd": 20, "conditional_total_usd": 50},
        "new_paid_api_calls": 0, "new_vm_launches": 0,
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    save(q.out / "preregister.json", plan)
    save(q.out / "initial-state.json", state)
    q.required("version", "GET", "/api/system/version")
    for role, username in user_map.items():
        q.required("create-user-" + role, "POST", "/ui/users", body={
            "name": username, "email": username + "@example.invalid",
            "password": q.creds[role], "admin": False, "groups": [],
            "profileUpdatable": False,
        })
        q.required("read-user-" + role, "GET", "/ui/users/" + username)
    policies = {}
    repo_configs = {}
    for arm in ARMS:
        for repo in sorted(set(arm_repos[arm].values())):
            config = {
                "type": "remoteRepoConfig", "general": {"repoKey": repo},
                "basic": {"url": "http://j-upstream:8000", "includesPattern": "**/*",
                          "layout": "simple-default", "offline": False},
                "advanced": {
                    "storeArtifactsLocally": arm != "no-store",
                    "cache": {"missedRetrievalCachePeriodSecs": 0 if arm in ("negative-off", "both") else 1800},
                },
                "typeSpecific": {"repoType": "Generic", "listRemoteFolderItems": False},
            }
            q.required("create-" + repo, "POST", "/ui/admin/repositories", body=config)
            _, text = q.required("config-" + repo, "GET", "/ui/admin/repositories/remote/" + repo)
            stored = json.loads(text)
            if stored["advanced"]["storeArtifactsLocally"] != (arm != "no-store"):
                raise RuntimeError("Local storage setting did not persist")
            if stored["advanced"]["cache"]["missedRetrievalCachePeriodSecs"] != config["advanced"]["cache"]["missedRetrievalCachePeriodSecs"]:
                raise RuntimeError("Negative cache setting did not persist")
            repo_configs[repo] = stored
            roles = ["sender", "receiver"] if arm != "partitioned" else [
                role for role in ("sender", "receiver") if arm_repos[arm][role] == repo]
            grants = [{"principal": user_map[role], "actions": ["read"]} for role in roles]
            grants.append({"principal": user_map["prefetch"], "actions": ["read", "upload"]})
            target = {
                "name": repo + "-perm",
                "repoPermission": {"repoKeys": [repo], "includePatterns": ["**"],
                                   "userPermissionActions": grants, "groupPermissionActions": []},
            }
            q.required("grant-" + repo, "POST", "/ui/permissiontargets", body=target)
            _, text = q.required("grants-" + repo, "GET", "/ui/permissiontargets/" + repo + "-perm")
            saved = json.loads(text)
            save(q.out / ("permission-" + repo + ".json"), saved)
            policies[repo] = {"block_metadata": arm in ("metadata-blocked", "both"), "arm": arm}
    state["configs"] = repo_configs
    save(STATE, state)
    save(ROOT / "gateway.json", {"run": run, "repos": policies})
    save(q.out / "gateway-policy.json", {"run": run, "repos": policies})
    print(json.dumps({"run": run, "users": len(user_map), "repos": len(repo_configs),
                      "evidence": str(q.out)}, indent=2), flush=True)


def connectivity(q):
    tests = []
    code = r"""
import json, socket
out=[]
for host,port in [("j-gateway",8000),("art-a",8082),("j-upstream",8000),
                  ("j-sender",22),("j-receiver",22),("169.254.169.254",80),
                  ("1.1.1.1",443)]:
 try:
  c=socket.create_connection((host,port),timeout=1);c.close();result="connected"
 except Exception as e: result=type(e).__name__
 out.append({"host":host,"port":port,"result":result})
print(json.dumps(out))
"""
    for container in ("j-sender", "j-receiver"):
        p = subprocess.run(["docker", "exec", container, "python3", "-c", code],
                           text=True, capture_output=True, timeout=15, check=True)
        result = json.loads(p.stdout)
        if result[0]["result"] != "connected" or any(
                row["result"] == "connected" for row in result[1:]):
            raise RuntimeError("Client isolation check failed")
        tests.append({"container": container, "checks": result})
    save(q.out / "connectivity.json", tests)


def prepare(q, state):
    result = []
    for arm in ARMS:
        pair = state["repos"][arm]
        for repo in sorted(set(pair.values())):
            q.required(arm + "-warm-" + repo, "GET", f"/{repo}/present/common.bin", "prefetch")
        if arm == "offline":
            repo = pair["sender"]
            config = copy.deepcopy(state["configs"][repo])
            config["basic"]["offline"] = True
            q.required("set-offline", "PUT", "/ui/admin/repositories", body=config)
            _, text = q.required("verify-offline", "GET", "/ui/admin/repositories/remote/" + repo)
            if json.loads(text)["basic"]["offline"] is not True:
                raise RuntimeError("Offline setting failed")
        for role in ("sender", "receiver"):
            repo = pair[role]
            record, _ = q.required(f"{arm}-{role}-ordinary", "GET", f"/{repo}/present/common.bin", role)
            if record["body_sha256"] != hashlib.sha256(PAYLOAD).hexdigest():
                raise RuntimeError("Ordinary artifact bytes changed")
            result.append({"arm": arm, "role": role, "check": "prewarmed",
                           "status": record["status"], "sha256": record["body_sha256"]})
            direct, _ = q.request(f"{arm}-{role}-direct-write", "MKCOL",
                                  f"/{repo}-cache/forbidden", role, direct=True)
            if direct["status"] not in (401, 403, 501):
                raise RuntimeError("A Read-only write succeeded or returned an unexplained status")
            gate, _ = q.request(f"{arm}-{role}-gateway-write", "PUT", f"/{repo}/forbidden", role)
            if gate["status"] != 405:
                raise RuntimeError("Method gate failed")
            fresh, _ = q.request(f"{arm}-{role}-fresh", "GET",
                                f"/{repo}/present/fresh-{role}.bin", role)
            result.append({"arm": arm, "role": role, "check": "fresh", "status": fresh["status"],
                           "sha256": fresh["body_sha256"]})
    for role, other in (("sender", "receiver"), ("receiver", "sender")):
        repo = state["repos"]["partitioned"][other]
        record, _ = q.request("partitioned-cross-" + role, "GET", f"/{repo}/present/common.bin", role)
        if record["status"] not in (401, 403):
            raise RuntimeError("Partition authorization failed")
    save(q.out / "utility.json", result)


def timing_trial(q, state, arm, delay, index, bit, phase, method="GET", send=True):
    pair = state["repos"][arm]
    relative = f"missing/{delay}/{state['run']}-{phase}-{arm}-{index}.bin"
    sender = None
    if bit and send:
        sender, _ = q.request(f"{phase}-{delay}-{arm}-{index}-prime", method,
                              f'/{pair["sender"]}/{relative}', "sender")
    receiver, _ = q.request(f"{phase}-{delay}-{arm}-{index}-receive", "GET",
                            f'/{pair["receiver"]}/{relative}', "receiver")
    if receiver["status"] != 404 or sender and sender["status"] != 404:
        raise RuntimeError("Missing fixture returned unexpected status")
    row = {"arm": arm, "delay_ms": delay, "index": index, "phase": phase,
           "scheduled_bit": bit, "expected_bit": bit if send else 0,
           "relative_path": relative, "sender_method": method,
           "sender_label": sender["label"] if sender else None,
           "receiver_label": receiver["label"], "duration_ns": receiver["duration_ns"],
           "status": receiver["status"], "body_bytes": receiver["body_bytes"]}
    return row


def timing(q, state, plan):
    thresholds = {}
    for delay in (150, 20):
        rows = []
        for i, bit in enumerate(plan["calibration_bits"][str(delay)]):
            row = timing_trial(q, state, "method-only", delay, i, bit, "calibration")
            append(q.out / "timing-calibration.jsonl", row)
            rows.append(row)
        medians = {str(bit): statistics.median(
            r["duration_ns"] for r in rows if r["expected_bit"] == bit) for bit in (0, 1)}
        threshold = (medians["0"] + medians["1"]) / 2
        thresholds[str(delay)] = {"threshold_ns": threshold, "class_medians_ns": medians,
                                  "frozen_utc_ns": time.time_ns()}
    save(q.out / "frozen-thresholds.json", thresholds)
    for delay in (150, 20):
        threshold = thresholds[str(delay)]["threshold_ns"]
        for arm in plan["timing_order"][str(delay)]:
            for i, bit in enumerate(plan["timing_bits"][str(delay)][arm]):
                row = timing_trial(q, state, arm, delay, i, bit, "holdout")
                row["decoded_bit"] = int(row["duration_ns"] < threshold)
                append(q.out / "timing-trials.jsonl", row)
            print(f"Timing holdout done: {delay} ms, {arm}", flush=True)
        for i in range(plan["no_sender_trials_per_delay"]):
            row = timing_trial(q, state, "method-only", delay, i, 0, "no-sender", send=False)
            row["decoded_bit"] = int(row["duration_ns"] < threshold)
            append(q.out / "timing-trials.jsonl", row)
        print(f"No-sender control done: {delay} ms", flush=True)
    threshold = thresholds["150"]["threshold_ns"]
    for phase, schedule, method, arms in (
        ("repeat", plan["repeat_bits"], "GET", ("method-only",)),
        ("head", plan["head_bits"], "HEAD", ("method-only", "both")),
    ):
        for arm in arms:
            for i, bit in enumerate(schedule):
                row = timing_trial(q, state, arm, 150, i, bit, phase, method=method)
                row["decoded_bit"] = int(row["duration_ns"] < threshold)
                append(q.out / "timing-trials.jsonl", row)
            print(f"Followup done: {phase}, {arm}", flush=True)


def stats(q, state, plan):
    for arm in ARMS:
        pair = state["repos"][arm]
        receiver_stats = f'/api/storage/{pair["receiver"]}-cache/present/common.bin?stats'
        for i, bit in enumerate(plan["statistics_bits"][arm]):
            before, text_before = q.request(f"stats-{arm}-{i}-before", "GET", receiver_stats, "receiver")
            sent = None
            if bit:
                sent, _ = q.required(f"stats-{arm}-{i}-sender", "GET",
                                      f'/{pair["sender"]}/present/common.bin', "sender")
            after, text_after = q.request(f"stats-{arm}-{i}-after", "GET", receiver_stats, "receiver")
            count_before = json.loads(text_before).get("downloadCount") if before["status"] == 200 else None
            count_after = json.loads(text_after).get("downloadCount") if after["status"] == 200 else None
            decoded = int(count_after > count_before) if count_before is not None and count_after is not None else None
            append(q.out / "stat-trials.jsonl", {
                "arm": arm, "index": i, "expected_bit": bit, "decoded_bit": decoded,
                "before_count": count_before, "after_count": count_after,
                "before_status": before["status"], "after_status": after["status"],
                "before_label": before["label"], "after_label": after["label"],
                "sender_label": sent["label"] if sent else None,
            })
        print(f"Statistics holdout done: {arm}", flush=True)


def execute():
    state = json.loads(STATE.read_text())
    q = Requests(state)
    started = q.out / "run-start.json"
    if started.exists():
        raise RuntimeError("Execution was already started. Inspect it; do not duplicate requests.")
    save(started, {"utc_ns": time.time_ns(), "pid": os.getpid()})
    plan = json.loads((q.out / "preregister.json").read_text())
    connectivity(q)
    prepare(q, state)
    timing(q, state, plan)
    stats(q, state, plan)
    save(q.out / "run-finished.json", {"utc_ns": time.time_ns(), "status": "complete"})
    print("CAMPAIGN COMPLETE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("setup", "run"))
    args = parser.parse_args()
    setup() if args.mode == "setup" else execute()
