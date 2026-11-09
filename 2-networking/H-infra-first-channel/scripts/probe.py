#!/usr/bin/env python3
"""Dedicated read-only channel probe. Run on the existing lab host."""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path.home() / "workspaces/artifactory-lab/h"
OUT = ROOT / "evidence"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "bodies").mkdir(exist_ok=True)
BASE = "http://127.0.0.1:18584/artifactory"
ADMIN = json.loads(
    (Path.home() / "workspaces/artifactory-lab/a/credentials.json").read_text()
)["admin"]
PRIVATE = ROOT / "credentials.json"
if not PRIVATE.exists():
    fd = os.open(PRIVATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({u: secrets.token_urlsafe(24) for u in ("h-sender", "h-receiver")}, f)
CREDS = json.loads(PRIVATE.read_text())
CREDS["admin"] = ADMIN
RUN = "h-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
REPO = RUN + "-local"
ROUTES = {}
CONTAINER_REQUEST = r"""
import base64, json, sys, time, urllib.error, urllib.request
d = json.load(sys.stdin)
h = {"Authorization": "Basic " + base64.b64encode(
    (d["user"] + ":" + d["password"]).encode()).decode(),
    "User-Agent": d["user_agent"]}
if d.get("content_type"):
    h["Content-Type"] = d["content_type"]
data = base64.b64decode(d["body_b64"]) if d.get("body_b64") is not None else None
q = urllib.request.Request(d["url"], method=d["method"], data=data, headers=h)
t0, m0 = time.time_ns(), time.monotonic_ns()
try:
    with urllib.request.urlopen(q, timeout=15) as r:
        status, body, headers = r.status, r.read(), dict(r.headers)
except urllib.error.HTTPError as e:
    status, body, headers = e.code, e.read(), dict(e.headers)
print(json.dumps({"status": status, "body_b64": base64.b64encode(body).decode(),
    "headers": headers, "start_utc_ns": t0, "end_utc_ns": time.time_ns(),
    "duration_ns": time.monotonic_ns() - m0}))
"""


def redact(text):
    for password in CREDS.values():
        text = text.replace(password, "<secret-redacted>")
    return text


def req(label, method, path, identity="admin", body=None):
    headers = {
        "Authorization": "Basic " + base64.b64encode(
            f"{identity}:{CREDS[identity]}".encode()
        ).decode(),
        "User-Agent": f"h-infra/{RUN}/{label}",
    }
    if isinstance(body, dict):
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif isinstance(body, str):
        data = body.encode()
        headers["Content-Type"] = "text/plain"
    else:
        data = body
    t0 = time.time_ns()
    mono0 = time.monotonic_ns()
    request = urllib.request.Request(BASE + path, method=method, data=data, headers=headers)
    measurement = {}
    if identity in ROUTES:
        container = ROUTES[identity]
        private_input = {
            "url": "http://art-a:8082/artifactory" + path,
            "method": method, "user": identity, "password": CREDS[identity],
            "body_b64": base64.b64encode(data).decode() if data is not None else None,
            "content_type": headers.get("Content-Type"), "user_agent": headers["User-Agent"],
        }
        p = subprocess.run(
            ["docker", "exec", "-i", container, "python3", "-c", CONTAINER_REQUEST],
            input=json.dumps(private_input), capture_output=True, text=True, timeout=25,
        )
        if p.returncode:
            raise RuntimeError("Container request failed: " + redact(p.stderr[-400:]))
        d = json.loads(p.stdout)
        status, payload, response_headers = d["status"], base64.b64decode(d["body_b64"]), d["headers"]
        measurement = {k: d[k] for k in ("start_utc_ns", "end_utc_ns", "duration_ns")}
    else:
        try:
            with urllib.request.urlopen(request, timeout=30) as r:
                status, payload, response_headers = r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            status, payload, response_headers = e.code, e.read(), dict(e.headers)
    duration = time.monotonic_ns() - mono0
    text = redact(payload.decode("utf-8", errors="replace"))
    filename = f"{RUN}-{label}.txt"
    (OUT / "bodies" / filename).write_text(text)
    record = {
        "run": RUN, "id": label, "identity": identity, "method": method,
        "path": path, "start_utc_ns": t0, "end_utc_ns": time.time_ns(),
        "duration_ns": duration, "status": status,
        "request_body": redact(data.decode(errors="replace")) if data else None,
        "response_body_file": "bodies/" + filename,
        "response_headers": response_headers, "user_agent": headers["User-Agent"],
        "response_sha256": hashlib.sha256(payload).hexdigest(),
    }
    if measurement:
        record["controller_start_utc_ns"] = t0
        record["controller_end_utc_ns"] = record["end_utc_ns"]
        record["controller_duration_ns"] = duration
        record.update(measurement)
        record["container"] = ROUTES[identity]
    with (OUT / "requests.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    print(label, identity, method, status, text[:250].replace("\n", " "), flush=True)
    return status, text


def require(result):
    status, text = result
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}: {text[:200]}")
    return text


def main():
    require(req("version", "GET", "/api/system/version"))
    for user in ("h-sender", "h-receiver"):
        status, _ = req("find-" + user, "GET", "/ui/users/" + user)
        if status != 200:
            require(req("create-" + user, "POST", "/ui/users", body={
                "name": user, "email": user + "@example.invalid",
                "password": CREDS[user], "admin": False, "groups": [],
                "profileUpdatable": False,
            }))
    require(req("create-local", "POST", "/ui/admin/repositories", body={
        "type": "localRepoConfig", "general": {"repoKey": REPO},
        "basic": {"includesPattern": "**/*", "layout": "simple-default"},
        "advanced": {}, "typeSpecific": {"repoType": "Generic"},
    }))
    require(req("grant-read", "POST", "/ui/permissiontargets", body={
        "name": RUN,
        "repoPermission": {
            "repoKeys": [REPO], "includePatterns": ["**"],
            "userPermissionActions": [
                {"principal": u, "actions": ["read"]}
                for u in ("h-sender", "h-receiver")
            ], "groupPermissionActions": [],
        },
    }))
    require(req("seed", "PUT", f"/{REPO}/signal.bin", body="harmless fixture\n"))
    for user in ("h-sender", "h-receiver"):
        require(req("stats-before-" + user, "GET",
                    f"/api/storage/{REPO}/signal.bin?stats", user))
        req("mkcol-denied-" + user, "MKCOL", f"/{REPO}/forbidden-{user}", user)
        req("put-denied-" + user, "PUT", f"/{REPO}/forbidden-{user}.txt", user, "x")
    require(req("sender-read", "GET", f"/{REPO}/signal.bin", "h-sender"))
    for i in range(4):
        req(f"receiver-stats-{i}", "GET", f"/api/storage/{REPO}/signal.bin?stats", "h-receiver")
        time.sleep(0.25)
    (OUT / "probe-state.json").write_text(json.dumps({"run": RUN, "repo": REPO}, indent=2))


if __name__ == "__main__":
    main()
