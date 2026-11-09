#!/usr/bin/env python3
"""Hub-side P1 fixture provisioning, sandbox requests, collection, and cleanup."""
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path.home() / "workspaces/artifactory-lab/p1"
STATE = ROOT / "state"
CREDS = ROOT / "credentials"
LOGS = ROOT / "logs"
BASE = "http://127.0.0.1:18584/artifactory"
ADMIN_CREDS = ROOT.parent / "a" / "credentials.json"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
CLIENT = r"""
import base64, hashlib, http.client, json, sys, time
d = json.load(sys.stdin)
headers = {"Authorization": "Basic " + base64.b64encode((d["user"] + ":" + d["password"]).encode()).decode(),
           "User-Agent": d["marker"]}
headers.update(d.get("headers", {}))
connection = http.client.HTTPConnection(d["host"], d["port"], timeout=20)
started, monotonic = time.time_ns(), time.monotonic_ns()
try:
    connection.request(d["method"], d["path"], headers=headers)
    response = connection.getresponse()
    body = response.read()
    print(json.dumps({"status": response.status, "headers": dict(response.getheaders()),
                      "body_sha256": hashlib.sha256(body).hexdigest(), "body_length": len(body),
                      "start_utc_ns": started, "end_utc_ns": time.time_ns(),
                      "elapsed_ms": (time.monotonic_ns() - monotonic) / 1e6}))
finally:
    connection.close()
"""
BURST = r"""
import base64, concurrent.futures, hashlib, http.client, json, sys, time
d = json.load(sys.stdin)
def one(index):
    headers = {"Authorization": "Basic " + base64.b64encode((d["user"] + ":" + d["password"]).encode()).decode(),
               "User-Agent": d["markers"][index]}
    connection = http.client.HTTPConnection(d["host"], d["port"], timeout=20)
    started, monotonic = time.time_ns(), time.monotonic_ns()
    try:
        connection.request("GET", d["path"], headers=headers)
        response = connection.getresponse()
        body = response.read()
        return {"status": response.status, "headers": dict(response.getheaders()),
                "body_sha256": hashlib.sha256(body).hexdigest(), "body_length": len(body),
                "start_utc_ns": started, "end_utc_ns": time.time_ns(),
                "elapsed_ms": (time.monotonic_ns() - monotonic) / 1e6}
    finally:
        connection.close()
with concurrent.futures.ThreadPoolExecutor(max_workers=len(d["markers"])) as pool:
    print(json.dumps(list(pool.map(one, range(len(d["markers"]))))))
"""


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


def run_ok(args, **kwargs):
    result = run(args, **kwargs)
    if result.returncode:
        raise RuntimeError(f"command failed: {args}: {result.stderr.strip()[-400:]}")
    return result.stdout


def ensure_dirs():
    for path in (STATE, CREDS, LOGS):
        path.mkdir(parents=True, exist_ok=True)


def check_id(run_id):
    if not NAME.fullmatch(run_id):
        raise ValueError("invalid run identifier")
    return run_id


def state_path(run_id):
    return STATE / f"{run_id}.json"


def credentials_path(run_id):
    return CREDS / f"{run_id}.json"


def load_state(run_id):
    return json.loads(state_path(run_id).read_text())


def load_credentials(run_id):
    return json.loads(credentials_path(run_id).read_text())


def admin_password():
    return json.loads(ADMIN_CREDS.read_text())["admin"]


def redact(value, secrets_to_remove):
    for secret in secrets_to_remove:
        value = value.replace(secret, "<secret-redacted>")
    return value


def admin_request(method, path, user, password, body=None, headers=None):
    request_headers = {
        "Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
        "User-Agent": "p1-provision",
    }
    if headers:
        request_headers.update(headers)
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    if isinstance(body, dict):
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as response:
        return response.code, response.read().decode(), dict(response.headers)


def free_gib():
    output = run_ok(["free", "-b"])
    for line in output.splitlines():
        if line.startswith("Mem:"):
            return int(line.split()[6]) / (1024 ** 3)
    raise RuntimeError("could not parse available memory")


def docker_network(name):
    run_ok(["docker", "network", "create", "--internal", "--opt",
            "com.docker.network.bridge.gateway_mode_ipv4=isolated", name])


def sandbox(name, network, extra=(), command=("python3", "-c", "import time; time.sleep(86400)")):
    run_ok(["docker", "run", "-d", "--name", name, "--network", network,
            "--user", "20001:20001", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--memory", "128m",
            "--memory-swap", "128m", "--cpus", "0.5", "--pids-limit", "48",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m", *extra, "python:3.12-slim", *command])


def ip_address(name, network):
    return json.loads(run_ok(["docker", "inspect", name]))[0]["NetworkSettings"]["Networks"][network]["IPAddress"]


def wait_ready(container, host, port=8000):
    probe = "import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),1).close()"
    for _ in range(40):
        if run(["docker", "exec", container, "python3", "-c", probe, host, str(port)]).returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("gateway did not become ready")


def provision(run_id, configuration, with_dummy=False):
    check_id(run_id)
    if configuration not in ("open", "combined", "separate"):
        raise ValueError("invalid configuration")
    ensure_dirs()
    if state_path(run_id).exists():
        raise RuntimeError("run identifier already exists")
    if free_gib() < 2:
        raise RuntimeError("available memory below 2 GiB")

    prefix = f"p1-{run_id}-"
    nets = {"alice": prefix + "net-alice", "bob": prefix + "net-bob", "services": prefix + "net-services"}
    containers = {role: prefix + role for role in ("alice", "bob", "gateway", "upstream")}
    users = {role: prefix + role for role in ("alice", "bob", "prefetch")}
    repositories = (
        {"shared": prefix + "shared"} if configuration != "separate"
        else {"alice": prefix + "alice", "bob": prefix + "bob"}
    )
    passwords = {username: "Aa0_" + secrets.token_urlsafe(24) for username in users.values()}
    file_descriptor = os.open(credentials_path(run_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(file_descriptor, "w") as stream:
        json.dump(passwords, stream)

    for network in nets.values():
        docker_network(network)
    sandbox(containers["alice"], nets["alice"])
    sandbox(containers["bob"], nets["bob"])
    sandbox(containers["upstream"], nets["services"],
            extra=("-v", f"{ROOT / 'upstream.py'}:/upstream.py:ro"),
            command=("python3", "/upstream.py"))

    origin_binding = {
        ip_address(containers["alice"], nets["alice"]): users["alice"],
        ip_address(containers["bob"], nets["bob"]): users["bob"],
    }
    gateway_config = {
        "run_id": run_id,
        "origin_binding": origin_binding,
        "repos": {repo: {"block_storage": configuration != "open"} for repo in repositories.values()},
    }
    gateway_config_path = ROOT / f"gateway-{run_id}.json"
    gateway_config_path.write_text(json.dumps(gateway_config))
    sandbox(containers["gateway"], nets["alice"],
            extra=("-v", f"{ROOT / 'gateway.py'}:/gateway.py:ro",
                   "-v", f"{gateway_config_path}:/gateway.json:ro"),
            command=("python3", "/gateway.py"))
    for network in (nets["bob"], nets["services"]):
        run_ok(["docker", "network", "connect", network, containers["gateway"]])
    run_ok(["docker", "network", "connect", nets["services"], "art-a"])
    wait_ready(containers["alice"], containers["gateway"])

    if with_dummy:
        for role in ("alice", "bob"):
            dummy_network = prefix + f"net-dummy-{role}"
            nets[f"dummy_{role}"] = dummy_network
            docker_network(dummy_network)
            run_ok(["docker", "network", "connect", dummy_network, containers[role]])
            name = prefix + f"dummy-{role}"
            containers[f"dummy_{role}"] = name
            sandbox(name, dummy_network, extra=("-v", f"{ROOT / 'dummy.py'}:/dummy.py:ro"),
                    command=("python3", "/dummy.py"))

    admin = admin_password()
    provision_log = []

    def call(label, method, path, body=None):
        status, text, headers = admin_request(method, path, "admin", admin, body)
        provision_log.append({"label": label, "method": method, "path": path, "status": status})
        if status not in (200, 201):
            detail = redact(text, [admin, *passwords.values()])[:300]
            raise RuntimeError(f"{label} failed with HTTP {status}: {detail}")
        return text, headers

    for role, username in users.items():
        call(f"create-user-{role}", "POST", "/ui/users", {
            "name": username, "email": username + "@example.invalid", "password": passwords[username],
            "admin": False, "groups": [], "profileUpdatable": False,
        })

    miss_period = 0 if configuration == "combined" else 1800
    for repo in repositories.values():
        call("create-repo", "POST", "/ui/admin/repositories", {
            "type": "remoteRepoConfig", "general": {"repoKey": repo},
            "basic": {"url": f"http://{containers['upstream']}:8000", "includesPattern": "**/*",
                      "layout": "simple-default", "offline": False},
            "advanced": {"storeArtifactsLocally": True,
                         "cache": {"missedRetrievalCachePeriodSecs": miss_period}},
            "typeSpecific": {"repoType": "Generic", "listRemoteFolderItems": False},
        })
        saved, _ = call("read-repo", "GET", "/ui/admin/repositories/remote/" + repo)
        if json.loads(saved)["advanced"]["cache"]["missedRetrievalCachePeriodSecs"] != miss_period:
            raise RuntimeError("negative-cache period did not persist")
        readers = (
            [users["alice"], users["bob"], users["prefetch"]] if configuration != "separate"
            else [users["prefetch"], users[[role for role, target in repositories.items() if target == repo][0]]]
        )
        actions = [
            {"principal": reader, "actions": ["read", "upload"] if reader == users["prefetch"] else ["read"]}
            for reader in readers
        ]
        call("create-permission", "POST", "/ui/permissiontargets", {
            "name": repo + "-perm",
            "repoPermission": {"repoKeys": [repo], "includePatterns": ["**"],
                               "userPermissionActions": actions, "groupPermissionActions": []},
        })

    etag = None
    for repo in repositories.values():
        for value in range(16):
            status, _, headers = admin_request(
                "GET", f"/{repo}/pkg-{value:02d}.bin", users["prefetch"], passwords[users["prefetch"]])
            provision_log.append({"label": f"prewarm-{repo}-{value:02d}", "method": "GET",
                                  "path": f"/{repo}/pkg-{value:02d}.bin", "status": status})
            if status != 200:
                raise RuntimeError(f"prewarm failed: HTTP {status}")
            etag = etag or headers.get("Etag") or headers.get("ETag")
    if not etag:
        raise RuntimeError("prewarm response did not include an ETag")
    state = {
        "run_id": run_id, "configuration": configuration, "repositories": repositories,
        "users": users, "containers": containers, "networks": nets,
        "negative_cache_period_seconds": miss_period, "storage_blocked": configuration != "open",
        "fixture_etag": etag, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state_path(run_id).write_text(json.dumps(state, indent=2))
    result = {
        "run_id": run_id, "configuration": configuration, "repositories": repositories,
        "users": users, "negative_cache_period_seconds": miss_period,
        "storage_blocked": configuration != "open", "prewarm_count": len(repositories) * 16,
        "provision_log": provision_log,
    }
    return result


def allowed_path(path, state, actor):
    parsed = urllib.parse.urlsplit(path)
    if not path.startswith("/") or parsed.scheme or parsed.netloc or any(c in path for c in "\r\n"):
        raise ValueError("invalid path")
    decoded = urllib.parse.unquote(parsed.path)
    if ".." in decoded.split("/") or "\\" in decoded:
        raise ValueError("path escapes repository")
    repo = state["repositories"].get(actor) or state["repositories"].get("shared")
    permitted = (f"/{repo}", f"/{repo}-cache", f"/api/storage/{repo}", f"/api/storage/{repo}-cache")
    if decoded not in permitted and not any(decoded.startswith(item + "/") for item in permitted):
        raise ValueError("path is outside actor repository")


def record_action(run_id, actor, trial_id, sequence, method, path, response, target="gateway", note=None):
    state = load_state(run_id)
    marker = f"p1/{run_id}/{trial_id}/{actor}/{sequence}"
    row = {
        "run_id": run_id, "trial_id": trial_id, "actor": actor, "sequence": sequence,
        "method": method, "path": path, "target": target, "marker": marker, "note": note, **response,
    }
    with (LOGS / f"{run_id}-actions.jsonl").open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    return row


def request(run_id, actor, trial_id, sequence, method, path, headers=None, note=None, target="gateway"):
    check_id(run_id)
    state = load_state(run_id)
    if actor not in ("alice", "bob") or method not in ("GET", "HEAD"):
        raise ValueError("invalid actor or method")
    if target == "gateway":
        allowed_path(path, state, actor)
        host, port, request_path = state["containers"]["gateway"], 8000, "/artifactory" + path
    elif target == "dummy":
        host, port, request_path = state["containers"][f"dummy_{actor}"], 8000, path
    else:
        raise ValueError("invalid target")
    credentials = load_credentials(run_id)
    username = state["users"][actor]
    marker = f"p1/{run_id}/{trial_id}/{actor}/{sequence}"
    private = {"host": host, "port": port, "path": request_path, "user": username,
               "password": credentials[username], "method": method, "marker": marker, "headers": headers or {}}
    result = run(["docker", "exec", "-i", state["containers"][actor], "python3", "-c", CLIENT],
                 input=json.dumps(private), timeout=25)
    if result.returncode:
        raise RuntimeError(redact(result.stderr[-500:], [credentials[username]]))
    return record_action(run_id, actor, trial_id, sequence, method, path, json.loads(result.stdout), target, note)


def burst(run_id, actor, trial_id, first_sequence, path, count, note=None):
    state = load_state(run_id)
    allowed_path(path, state, actor)
    credentials = load_credentials(run_id)
    username = state["users"][actor]
    markers = [f"p1/{run_id}/{trial_id}/{actor}/{first_sequence + index}" for index in range(count)]
    private = {"host": state["containers"]["gateway"], "port": 8000, "path": "/artifactory" + path,
               "user": username, "password": credentials[username], "markers": markers}
    result = run(["docker", "exec", "-i", state["containers"][actor], "python3", "-c", BURST],
                 input=json.dumps(private), timeout=25)
    if result.returncode:
        raise RuntimeError(redact(result.stderr[-500:], [credentials[username]]))
    return [record_action(run_id, actor, trial_id, first_sequence + index, "GET", path, response,
                          "gateway", note) for index, response in enumerate(json.loads(result.stdout))]


def docker_log(container):
    result = run(["docker", "logs", container])
    if result.returncode:
        raise RuntimeError(f"could not read logs for {container}")
    records = []
    for line in (result.stdout + result.stderr).splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"raw": line})
    return records


def collect(run_id):
    state = load_state(run_id)
    prefix = f"p1/{run_id}/"
    repos = list(state["repositories"].values())
    request_lines = [line for line in run_ok(["docker", "exec", "art-a", "cat",
                                              "/var/opt/jfrog/artifactory/log/artifactory-request.log"]).splitlines()
                     if prefix in line or any(repo in line for repo in repos)]
    router_lines = []
    for line in run_ok(["docker", "exec", "art-a", "cat",
                        "/var/opt/jfrog/artifactory/log/router-request.log"]).splitlines():
        if prefix in line or any(repo in line for repo in repos):
            try:
                router_lines.append(json.loads(line))
            except json.JSONDecodeError:
                router_lines.append({"raw": line})
    actions = (LOGS / f"{run_id}-actions.jsonl")
    result = {
        "run_id": run_id, "gateway_log": docker_log(state["containers"]["gateway"]),
        "upstream_log": docker_log(state["containers"]["upstream"]),
        "artifactory_request_log_lines": request_lines,
        "artifactory_router_log": router_lines,
        "client_actions": [json.loads(line) for line in actions.read_text().splitlines()] if actions.exists() else [],
        "collected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return result


def cleanup(run_id):
    check_id(run_id)
    prefix = f"p1-{run_id}-"
    state = load_state(run_id) if state_path(run_id).exists() else None
    removed = []
    names = ["alice", "bob", "gateway", "upstream", "dummy-alice", "dummy-bob"]
    for suffix in names:
        result = run(["docker", "rm", "-f", prefix + suffix])
        removed.append({"name": prefix + suffix, "returncode": result.returncode})
    services = prefix + "net-services"
    result = run(["docker", "network", "disconnect", services, "art-a"])
    removed.append({"name": services + "->art-a", "returncode": result.returncode})
    for suffix in ("net-alice", "net-bob", "net-services", "net-dummy-alice", "net-dummy-bob"):
        result = run(["docker", "network", "rm", prefix + suffix])
        removed.append({"name": prefix + suffix, "returncode": result.returncode})
    config = ROOT / f"gateway-{run_id}.json"
    if config.exists():
        config.unlink()
    containers = run_ok(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    networks = run_ok(["docker", "network", "ls", "--format", "{{.Name}}"]).splitlines()
    attached = json.loads(run_ok(["docker", "inspect", "art-a", "--format", "{{json .NetworkSettings.Networks}}"]))
    remaining_containers = [name for name in containers if name.startswith(prefix)]
    remaining_networks = [name for name in networks if name.startswith(prefix)]
    art_a_networks = [name for name in attached if name.startswith(prefix)]
    result = {
        "run_id": run_id, "removed": removed, "containers_readback": remaining_containers,
        "networks_readback": remaining_networks, "art_a_p1_networks": art_a_networks,
        "absence_proved": not remaining_containers and not remaining_networks and not art_a_networks,
        "fixtures_left_in_place": state["repositories"] if state else None,
    }
    return result


@contextmanager
def mutation_lock(run_id):
    ensure_dirs()
    with (STATE / f"{run_id}.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "provision":
        with mutation_lock(sys.argv[2]):
            print(json.dumps(provision(sys.argv[2], sys.argv[3], "--dummy" in sys.argv[4:])))
    elif command == "collect":
        print(json.dumps(collect(sys.argv[2])))
    elif command == "cleanup":
        with mutation_lock(sys.argv[2]):
            print(json.dumps(cleanup(sys.argv[2])))
    else:
        raise SystemExit("service.py is imported by run.py for request execution")
