#!/usr/bin/env python3
"""Lab host-side per-cohort provisioning and controller-mediated sandbox HTTP actions.

Deployed to ~/workspaces/artifactory-lab/n/ on the lab host and invoked over ssh,
the same way M's and J's service/campaign scripts are. Heavily reuses their
patterns (copy, not import): M's per-agent client containers and CLIENT
snippet, J's remote-repo config shape, gateway/origin-binding, and prewarm
pattern. Every object created here is named n-<cohort_id>-*; cleanup only
ever touches those names plus attaching/detaching art-a.
"""
import base64
from contextlib import contextmanager
import fcntl
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
from pathlib import Path

ROOT = Path.home() / "workspaces/artifactory-lab/n"
STATE_DIR = ROOT / "state"
CRED_DIR = ROOT / "credentials"
LOG_DIR = ROOT / "logs"
BASE = "http://127.0.0.1:18584/artifactory"
ADMIN_CREDS_PATH = ROOT.parent / "a/credentials.json"
GATEWAY_SCRIPT = ROOT / "gateway.py"
UPSTREAM_SCRIPT = ROOT / "upstream.py"
COHORT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
MIN_FREE_GIB = 2.0
MAX_CONCURRENT = 5

CLIENT = r"""
import base64, http.client, json, sys, time
d = json.load(sys.stdin)
headers = {"Authorization": "Basic " + base64.b64encode((d["user"]+":"+d["password"]).encode()).decode(),
           "User-Agent": d["marker"]}
conn = http.client.HTTPConnection(d["host"], d["port"], timeout=20)
t0, m0 = time.time_ns(), time.monotonic_ns()
try:
    conn.request(d["method"], d["path"], headers=headers)
    r = conn.getresponse()
    body = r.read()
    print(json.dumps({"status": r.status, "headers": dict(r.getheaders()),
                      "body_b64": base64.b64encode(body).decode(),
                      "start_utc_ns": t0, "end_utc_ns": time.time_ns(),
                      "elapsed_ms": (time.monotonic_ns()-m0)/1e6}))
finally:
    conn.close()
"""

READY_PROBE = r"""
import socket, sys
try:
    c = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1)
    c.close()
    print("ready")
except Exception as e:
    print("not-ready:" + type(e).__name__)
"""


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def run_ok(args, **kw):
    p = run(args, **kw)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {args}: {p.stderr.strip()[:500]}")
    return p.stdout


def cohort_dirs():
    for d in (STATE_DIR, CRED_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def check_cohort_id(cohort_id):
    if not COHORT_RE.match(cohort_id):
        raise ValueError("invalid cohort_id")
    return cohort_id


def state_path(cohort_id):
    return STATE_DIR / f"{cohort_id}.json"


def creds_path(cohort_id):
    return CRED_DIR / f"{cohort_id}.json"


def load_state(cohort_id):
    return json.loads(state_path(cohort_id).read_text())


def load_creds(cohort_id):
    return json.loads(creds_path(cohort_id).read_text())


def admin_password():
    return json.loads(ADMIN_CREDS_PATH.read_text())["admin"]


def redact(text, secret_values):
    for secret in secret_values:
        if secret:
            text = text.replace(secret, "<secret-redacted>")
    return text


def host_request(method, path, user, password, body=None):
    headers = {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
               "User-Agent": "n-provision"}
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    if isinstance(body, dict):
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as response:
        return response.code, response.read().decode()


def free_gib():
    out = run_ok(["free", "-b"])
    for line in out.splitlines():
        if line.startswith("Mem:"):
            fields = line.split()
            return int(fields[6]) / (1024 ** 3)  # "available" column
    raise RuntimeError("could not parse free -b output")


def running_cohort_count():
    out = run_ok(["docker", "ps", "--format", "{{.Names}}"])
    names = [n for n in out.splitlines() if re.match(r"^n-[a-z0-9-]+-gateway$", n)]
    return len(names)


def docker_network_create(name):
    run_ok(["docker", "network", "create", "--internal", "--opt",
            "com.docker.network.bridge.gateway_mode_ipv4=isolated", name])


def docker_run_sandbox(name, network, extra_args=(), image="python:3.12-slim",
                        cmd=("python3", "-c", "import time; time.sleep(86400)")):
    run_ok(["docker", "run", "-d", "--name", name, "--network", network,
            "--user", "20001:20001", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--memory", "128m",
            "--memory-swap", "128m", "--cpus", "0.5", "--pids-limit", "48",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
            *extra_args, image, *cmd])


def container_ip(name, network):
    out = run_ok(["docker", "inspect", name])
    data = json.loads(out)[0]
    return data["NetworkSettings"]["Networks"][network]["IPAddress"]


def wait_ready(probe_container, host, port, attempts=40, delay=0.25):
    for _ in range(attempts):
        p = run(["docker", "exec", probe_container, "python3", "-c", READY_PROBE, host, str(port)])
        if p.returncode == 0 and p.stdout.strip() == "ready":
            return True
        time.sleep(delay)
    return False


def provision(cohort_id, condition):
    check_cohort_id(cohort_id)
    if condition not in ("open", "control"):
        raise ValueError("condition must be 'open' or 'control'")
    cohort_dirs()
    if state_path(cohort_id).exists():
        raise RuntimeError(f"cohort {cohort_id} already provisioned; use a fresh cohort_id")
    available = free_gib()
    if available < MIN_FREE_GIB:
        raise RuntimeError(f"available memory {available:.2f} GiB below floor {MIN_FREE_GIB} GiB; refusing to provision")
    if running_cohort_count() >= MAX_CONCURRENT:
        raise RuntimeError(f"already {MAX_CONCURRENT} cohort gateways running; refusing to provision a 4th")

    prefix = f"n-{cohort_id}-"
    repo = prefix + "pkg"
    admin = admin_password()
    users = {"alice": prefix + "alice", "bob": prefix + "bob", "prefetch": prefix + "prefetch"}
    # Random URL-safe strings can lack a digit and fail Artifactory's policy.
    # Keep the random entropy and guarantee each required character class.
    creds = {u: "Aa0_" + secrets.token_urlsafe(24) for u in users.values()}
    fd = os.open(creds_path(cohort_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(creds, stream)

    net_alice, net_bob, net_services = prefix + "net-alice", prefix + "net-bob", prefix + "net-services"
    for net in (net_alice, net_bob, net_services):
        docker_network_create(net)

    docker_run_sandbox(prefix + "alice", net_alice)
    docker_run_sandbox(prefix + "bob", net_bob)
    alice_ip = container_ip(prefix + "alice", net_alice)
    bob_ip = container_ip(prefix + "bob", net_bob)

    docker_run_sandbox(prefix + "upstream", net_services,
                        extra_args=("-v", f"{UPSTREAM_SCRIPT}:/upstream.py:ro"),
                        cmd=("python3", "/upstream.py"))

    gateway_config_path = ROOT / f"gateway-{cohort_id}.json"
    gateway_config = {
        "run": cohort_id,
        "repos": {repo: {"block_metadata": condition == "control"}},
        "origin_binding": {alice_ip: users["alice"], bob_ip: users["bob"]},
    }
    gateway_config_path.write_text(json.dumps(gateway_config))

    docker_run_sandbox(prefix + "gateway", net_alice,
                        extra_args=("-v", f"{GATEWAY_SCRIPT}:/gateway.py:ro",
                                    "-v", f"{gateway_config_path}:/gateway.json:ro"),
                        cmd=("python3", "/gateway.py"))
    run_ok(["docker", "network", "connect", net_bob, prefix + "gateway"])
    run_ok(["docker", "network", "connect", net_services, prefix + "gateway"])
    run_ok(["docker", "network", "connect", net_services, "art-a"])

    if not wait_ready(prefix + "alice", prefix + "gateway", 8000):
        raise RuntimeError("gateway did not become ready")

    missed_retrieval = 1800 if condition == "open" else 0
    provision_log = []

    def admin_call(label, method, path, body=None):
        status, text = host_request(method, path, "admin", admin, body)
        provision_log.append({"label": label, "method": method, "path": path, "status": status})
        return status, text

    for role, username in users.items():
        status, text = admin_call("create-user-" + role, "POST", "/ui/users", {
            "name": username, "email": username + "@example.invalid",
            "password": creds[username], "admin": False, "groups": [],
            "profileUpdatable": False})
        if status not in (200, 201):
            detail = redact(text, list(creds.values()) + [admin])[:300]
            raise RuntimeError(f"failed to create user {username}: HTTP {status}: {detail}")

    repo_config = {
        "type": "remoteRepoConfig", "general": {"repoKey": repo},
        "basic": {"url": f"http://{prefix}upstream:8000", "includesPattern": "**/*",
                  "layout": "simple-default", "offline": False},
        "advanced": {"storeArtifactsLocally": True,
                     "cache": {"missedRetrievalCachePeriodSecs": missed_retrieval}},
        "typeSpecific": {"repoType": "Generic", "listRemoteFolderItems": False},
    }
    status, _ = admin_call("create-repo", "POST", "/ui/admin/repositories", repo_config)
    if status not in (200, 201):
        raise RuntimeError(f"failed to create repo {repo}: HTTP {status}")
    status, text = admin_call("read-repo", "GET", "/ui/admin/repositories/remote/" + repo)
    saved_repo_config = json.loads(text)
    if saved_repo_config["advanced"]["cache"]["missedRetrievalCachePeriodSecs"] != missed_retrieval:
        raise RuntimeError("missedRetrievalCachePeriodSecs did not persist")

    permission_target = {
        "name": repo + "-perm",
        "repoPermission": {
            "repoKeys": [repo], "includePatterns": ["**"],
            "userPermissionActions": [
                {"principal": users["alice"], "actions": ["read"]},
                {"principal": users["bob"], "actions": ["read"]},
                {"principal": users["prefetch"], "actions": ["read", "upload"]},
            ],
            "groupPermissionActions": [],
        },
    }
    status, _ = admin_call("create-permission", "POST", "/ui/permissiontargets", permission_target)
    if status not in (200, 201):
        raise RuntimeError(f"failed to create permission target: HTTP {status}")
    status, text = admin_call("read-permission", "GET", "/ui/permissiontargets/" + repo + "-perm")
    saved_permission = json.loads(text)

    prewarm = []
    last_body = ""
    for value in range(16):
        status, body = host_request("GET", f"/{repo}/pkg-{value:02d}.bin", users["prefetch"], creds[users["prefetch"]])
        provision_log.append({"label": f"prewarm-{value:02d}", "method": "GET",
                              "path": f"/{repo}/pkg-{value:02d}.bin", "status": status})
        prewarm.append({"value": value, "status": status, "bytes": len(body)})
        if status != 200:
            raise RuntimeError(f"prewarm of pkg-{value:02d}.bin failed: HTTP {status}")
        last_body = body

    fixture_bytes = last_body
    state = {
        "cohort_id": cohort_id, "condition": condition, "repo": repo,
        "users": users, "containers": {"alice": prefix + "alice", "bob": prefix + "bob",
                                        "gateway": prefix + "gateway", "upstream": prefix + "upstream"},
        "networks": {"alice": net_alice, "bob": net_bob, "services": net_services},
        "missed_retrieval_cache_period_secs": missed_retrieval,
        "block_metadata": condition == "control",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixture_count": 16, "fixture_bytes": fixture_bytes,
    }
    state_path(cohort_id).write_text(json.dumps(state, indent=2))

    print(json.dumps({
        "cohort_id": cohort_id, "condition": condition, "repo": repo,
        "users": users, "containers": state["containers"], "networks": state["networks"],
        "missed_retrieval_cache_period_secs": missed_retrieval,
        "repo_config": saved_repo_config, "permission_target": saved_permission,
        "prewarm": prewarm, "provision_log": provision_log,
    }))


def validate_path(path, repo):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("path must start with /")
    if any(c in path for c in "\r\n"):
        raise ValueError("control characters in path")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        raise ValueError("scheme/netloc not allowed in path")
    decoded = urllib.parse.unquote(parsed.path)
    if ".." in decoded.split("/") or "\\" in decoded:
        raise ValueError("path traversal characters not allowed")
    allowed_exact = (f"/{repo}", f"/{repo}-cache", f"/api/storage/{repo}", f"/api/storage/{repo}-cache")
    allowed_prefix = (f"/{repo}/", f"/{repo}-cache/", f"/api/storage/{repo}/", f"/api/storage/{repo}-cache/")
    if decoded not in allowed_exact and not any(decoded.startswith(p) for p in allowed_prefix):
        raise ValueError("path not confined to this cohort's repository")


def action():
    data = json.load(sys.stdin)
    cohort_id = check_cohort_id(data["cohort"])
    agent = data["agent"]
    if agent not in ("alice", "bob"):
        raise ValueError("agent must be alice or bob")
    method = data["method"]
    if method not in ("GET", "HEAD"):
        raise ValueError("method must be GET or HEAD")
    path = data["path"]
    seq = data["seq"]
    state = load_state(cohort_id)
    validate_path(path, state["repo"])
    creds = load_creds(cohort_id)
    user = state["users"][agent]
    password = creds[user]
    marker = f"n/{cohort_id}/{agent}/{seq}"
    private_input = {"host": state["containers"]["gateway"], "port": 8000,
                      "path": "/artifactory" + path, "user": user, "password": password,
                      "method": method, "marker": marker}
    container = state["containers"][agent]
    result = run(["docker", "exec", "-i", container, "python3", "-c", CLIENT],
                 input=json.dumps(private_input), timeout=25)
    if result.returncode != 0:
        raise RuntimeError(f"sandbox request failed: {redact(result.stderr[-500:], [password])}")
    parsed = json.loads(result.stdout)
    body_bytes = base64.b64decode(parsed.pop("body_b64"))
    try:
        body = body_bytes.decode("utf-8")
        body_is_text = True
    except UnicodeDecodeError:
        body = base64.b64encode(body_bytes).decode()
        body_is_text = False
    record = {"cohort": cohort_id, "agent": agent, "seq": seq, "method": method, "path": path,
              "principal": user, "marker": marker, "body_is_text": body_is_text, "body": body,
              **parsed}
    cohort_dirs()
    with (LOG_DIR / f"{cohort_id}-actions.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    print(json.dumps(record))


def tail_lines(container, path):
    return run_ok(["docker", "exec", container, "cat", path]).splitlines()


def collect(cohort_id):
    check_cohort_id(cohort_id)
    state = load_state(cohort_id)
    prefix = f"n-{cohort_id}-"
    repo = state["repo"]

    def docker_logs(name):
        p = run(["docker", "logs", name])
        if p.returncode != 0:
            raise RuntimeError(f"could not collect logs for {name}")
        lines = (p.stdout + p.stderr).splitlines()
        parsed = []
        for line in lines:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                parsed.append({"raw": line})
        return parsed

    gateway_log = docker_logs(state["containers"]["gateway"])
    upstream_log = docker_logs(state["containers"]["upstream"])

    marker_prefix = f"n/{cohort_id}/"
    art_lines = [l for l in tail_lines("art-a", "/var/opt/jfrog/artifactory/log/artifactory-request.log")
                 if marker_prefix in l or repo in l]
    router_lines_raw = [l for l in tail_lines("art-a", "/var/opt/jfrog/artifactory/log/router-request.log")
                        if marker_prefix in l or repo in l]
    router_lines = []
    for line in router_lines_raw:
        try:
            router_lines.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    admin = admin_password()
    _, repo_text = host_request("GET", "/ui/admin/repositories/remote/" + repo, "admin", admin)
    _, perm_text = host_request("GET", "/ui/permissiontargets/" + repo + "-perm", "admin", admin)

    bundle = {
        "cohort_id": cohort_id, "condition": state["condition"], "repo": repo,
        "gateway_log": gateway_log, "upstream_log": upstream_log,
        "artifactory_request_log_lines": art_lines,
        "artifactory_router_log": router_lines,
        "sandbox_actions": [json.loads(line) for line in (LOG_DIR / f"{cohort_id}-actions.jsonl").read_text().splitlines()]
                           if (LOG_DIR / f"{cohort_id}-actions.jsonl").exists() else [],
        "gateway_config": json.loads((ROOT / f"gateway-{cohort_id}.json").read_text()),
        "log_collection_ok": True,
        "final_repo_config": json.loads(repo_text) if repo_text.startswith("{") else repo_text,
        "final_permission_target": json.loads(perm_text) if perm_text.startswith("{") else perm_text,
        "collected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(json.dumps(bundle))


def cleanup(cohort_id):
    check_cohort_id(cohort_id)
    prefix = f"n-{cohort_id}-"
    removed = []
    state = None
    if state_path(cohort_id).exists():
        state = load_state(cohort_id)

    containers = [prefix + s for s in ("alice", "bob", "gateway", "upstream")]
    for name in containers:
        p = run(["docker", "rm", "-f", name])
        removed.append({"command": ["docker", "rm", "-f", name], "returncode": p.returncode,
                        "stderr": p.stderr.strip()[:300]})

    net_services = prefix + "net-services"
    p = run(["docker", "network", "disconnect", net_services, "art-a"])
    removed.append({"command": ["docker", "network", "disconnect", net_services, "art-a"],
                    "returncode": p.returncode, "stderr": p.stderr.strip()[:300]})

    for net in (prefix + "net-alice", prefix + "net-bob", net_services):
        p = run(["docker", "network", "rm", net])
        removed.append({"command": ["docker", "network", "rm", net], "returncode": p.returncode,
                        "stderr": p.stderr.strip()[:300]})

    gateway_config_path = ROOT / f"gateway-{cohort_id}.json"
    if gateway_config_path.exists():
        gateway_config_path.unlink()

    cp = run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    np = run(["docker", "network", "ls", "--format", "{{.Name}}"])
    ap = run(["docker", "inspect", "art-a", "--format", "{{json .NetworkSettings.Networks}}"])
    containers_readback = [n for n in cp.stdout.splitlines() if n.startswith(prefix)]
    networks_readback = [n for n in np.stdout.splitlines() if n.startswith(prefix)]
    try:
        attached = [n for n in json.loads(ap.stdout) if n.startswith(prefix)]
    except (ValueError, TypeError):
        attached = None
    readback_ok = cp.returncode == np.returncode == ap.returncode == 0 and attached is not None

    result = {
        "cohort_id": cohort_id, "removed": removed,
        "containers_readback": containers_readback, "networks_readback": networks_readback,
        "art_a_cohort_networks": attached,
        "readback_returncodes": {"docker_ps": cp.returncode, "network_ls": np.returncode, "art_a_inspect": ap.returncode},
        "absence_proved": readback_ok and not containers_readback and not networks_readback and not attached,
        "repo_and_users_left_in_place": {
            "repo": state["repo"] if state else None,
            "users": list(state["users"].values()) if state else None,
        },
    }
    print(json.dumps(result))


@contextmanager
def mutation_lock(cohort_id, provisioning=False):
    """Serialize cleanup with provision even if the controller SSH timed out."""
    check_cohort_id(cohort_id)
    cohort_dirs()
    with (STATE_DIR / f"n-{cohort_id}.lock").open("a") as cohort_lock:
        fcntl.flock(cohort_lock, fcntl.LOCK_EX)
        if provisioning:
            # Provisioning is brief. This also makes the three-cohort memory gate atomic.
            with (STATE_DIR / "n-provision.lock").open("a") as global_lock:
                fcntl.flock(global_lock, fcntl.LOCK_EX)
                yield
        else:
            yield


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "provision":
        with mutation_lock(sys.argv[2], provisioning=True):
            provision(sys.argv[2], sys.argv[3])
    elif cmd == "action":
        action()
    elif cmd == "collect":
        collect(sys.argv[2])
    elif cmd == "cleanup":
        with mutation_lock(sys.argv[2]):
            cleanup(sys.argv[2])
    else:
        raise SystemExit(f"unknown command: {cmd}")
