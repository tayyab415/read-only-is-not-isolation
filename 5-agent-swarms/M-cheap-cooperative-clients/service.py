#!/usr/bin/env python3
"""Private lab host provisioning and controller-mediated sandbox HTTP actions."""
import base64
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
PREFIX = ROOT.name
assert PREFIX in ("m", "mt")
OUT = ROOT / "evidence"
BASE = "http://127.0.0.1:18584/artifactory"


def host_request(method, path, user, password, body=None):
    headers = {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
               "User-Agent": "m-provision"}
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    if isinstance(body, dict):
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE+path, method=method, data=data, headers=headers)
    started = time.time_ns()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, text = response.status, response.read().decode()
    except urllib.error.HTTPError as response:
        status, text = response.code, response.read().decode()
    private = ROOT / "credentials.json"
    values = list(json.loads(private.read_text()).values()) if private.exists() else []
    def redact(value):
        for secret in values+[password]:
            value = value.replace(secret, "<secret-redacted>")
        return value
    record = {"method": method, "path": path, "identity": user, "status": status,
              "start_utc_ns": started, "end_utc_ns": time.time_ns(),
              "request_body": redact(data.decode()) if data else None,
              "response_body": redact(text)}
    with (OUT / "provision-requests.jsonl").open("a") as stream:
        stream.write(json.dumps(record)+"\n")
    return status, text


def provision():
    OUT.mkdir(parents=True, exist_ok=True)
    private = ROOT / "credentials.json"
    assert not (OUT / "state.json").exists(), "Use fresh fixtures, not a completed repeated provision"
    admin = json.loads((ROOT.parent / "a/credentials.json").read_text())["admin"]
    run = PREFIX+"-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    if private.exists():
        creds = json.loads(private.read_text())
    else:
        creds = {PREFIX+"-"+agent: secrets.token_urlsafe(24) for agent in ("alice", "bob")}
        fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(creds, stream)
    for user in creds:
        status, _ = host_request("GET", "/ui/users/"+user, "admin", admin)
        if status == 200:
            continue
        status, _ = host_request("POST", "/ui/users", "admin", admin, {
            "name": user, "email": user+"@example.invalid", "password": creds[user],
            "admin": False, "groups": [], "profileUpdatable": False})
        assert status in (200, 201)
    repos = [run+"-shared", run+"-alice", run+"-bob"]
    for repo in repos:
        status, _ = host_request("POST", "/ui/admin/repositories", "admin", admin, {
            "type": "localRepoConfig", "general": {"repoKey": repo},
            "basic": {"includesPattern": "**/*", "layout": "simple-default"},
            "advanced": {}, "typeSpecific": {"repoType": "Generic"}})
        assert status in (200, 201)
        for value in range(16):
            status, _ = host_request("PUT", f"/{repo}/pkg-{value:02d}.bin", "admin", admin,
                                     b"approved calibration package bytes\n")
            assert status == 201
    for suffix, principals in (("shared", [PREFIX+"-alice", PREFIX+"-bob"]),
                               ("alice", [PREFIX+"-alice"]), ("bob", [PREFIX+"-bob"])):
        status, _ = host_request("POST", "/ui/permissiontargets", "admin", admin, {
            "name": run+"-"+suffix, "repoPermission": {
                "repoKeys": [run+"-"+suffix], "includePatterns": ["**"],
                "userPermissionActions": [{"principal": u, "actions": ["read"]} for u in principals],
                "groupPermissionActions": []}})
        assert status in (200, 201)
        status, text = host_request("GET", "/ui/permissiontargets/"+run+"-"+suffix, "admin", admin)
        assert status == 200
        (OUT / ("permission-"+suffix+".json")).write_text(text)
    for short in ("alice", "bob"):
        network = PREFIX+"-net-"+short
        subprocess.run(["docker", "network", "create", "--internal", "--opt",
                        "com.docker.network.bridge.gateway_mode_ipv4=isolated", network], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "run", "-d", "--name", PREFIX+"-"+short, "--network", network,
                        "--user", "20001:20001", "--read-only", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges:true", "--memory", "128m",
                        "--memory-swap", "128m", "--cpus", "0.25", "--pids-limit", "32",
                        "python:3.12-slim", "python3", "-c", "import time; time.sleep(3600)"],
                       check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "network", "connect", network, "art-a"], check=True)
    state = {"run": run, "repos": repos, "users": list(creds), "fixture_count": 16,
             "fixture_bytes": "approved calibration package bytes\n"}
    (OUT / "state.json").write_text(json.dumps(state, indent=2))
    print(json.dumps(state))


CLIENT = """
import base64,json,sys,time,urllib.request,urllib.error
d=json.load(sys.stdin)
headers={'Authorization':'Basic '+base64.b64encode((d['user']+':'+d['password']).encode()).decode(),
         'User-Agent':d['user_agent']}
q=urllib.request.Request(d['url'],method=d['method'],headers=headers)
wall,mono=time.time_ns(),time.monotonic_ns()
try:
 with urllib.request.urlopen(q,timeout=15) as r: status,body=r.status,r.read().decode()
except urllib.error.HTTPError as r: status,body=r.code,r.read().decode()
print(json.dumps({'status':status,'body':body,'start_utc_ns':wall,'end_utc_ns':time.time_ns(),
                  'duration_ns':time.monotonic_ns()-mono}))
"""


def action():
    data = json.load(sys.stdin)
    assert data["agent"] in ("alice", "bob")
    assert data["condition"] in ("shared", "separate")
    state = json.loads((OUT / "state.json").read_text())
    repo = state["run"]+"-"+("shared" if data["condition"] == "shared" else data["agent"])
    method, path = data["method"], data["path"]
    assert method in ("GET", "HEAD")
    parsed = urllib.parse.urlsplit(path)
    decoded = urllib.parse.unquote(parsed.path)
    assert not parsed.scheme and not parsed.netloc
    assert decoded.startswith("/"+repo+"/") or decoded == "/"+repo or (
        decoded.startswith("/api/storage/"+repo+"/") or decoded == "/api/storage/"+repo)
    assert ".." not in decoded.split("/") and "\\" not in decoded
    assert not any(c in path for c in "\r\n")
    user = PREFIX+"-"+data["agent"]
    creds = json.loads((ROOT / "credentials.json").read_text())
    tag = "m-controller/"+state["run"]+"/"+data["condition"]+"/"+data["agent"]+"/"+str(data["turn"])
    private = {"url": "http://art-a:8082/artifactory"+path, "user": user,
               "password": creds[user], "method": method, "user_agent": tag}
    result = subprocess.run(["docker", "exec", "-i", PREFIX+"-"+data["agent"], "python3", "-c", CLIENT],
                            input=json.dumps(private), text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, "Sandbox request failed"
    record = {**data, **json.loads(result.stdout), "principal": user, "user_agent": tag}
    with (OUT / ("requests-"+data["agent"]+".jsonl")).open("a") as stream:
        stream.write(json.dumps(record)+"\n")
    print(json.dumps(record))


def cleanup():
    result = []
    for short in ("alice", "bob"):
        network = PREFIX+"-net-"+short
        for args in (["docker", "rm", "-f", PREFIX+"-"+short],
                     ["docker", "network", "disconnect", network, "art-a"],
                     ["docker", "network", "rm", network]):
            p = subprocess.run(args, capture_output=True, text=True)
            result.append({"command": args, "returncode": p.returncode})
    (OUT / "cleanup.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    assert all(r["returncode"] == 0 for r in result)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    {"provision": provision, "action": action, "cleanup": cleanup}[sys.argv[1]]()
