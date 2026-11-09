#!/usr/bin/env python3
"""HTTP recorder. Secrets stay in A's private credentials file on the VM."""
import datetime
import hashlib
import json
from pathlib import Path
import time
import uuid
import requests

LAB = Path.home() / "workspaces/artifactory-lab/a"
OUT = LAB / "evidence"
BASE = "http://127.0.0.1:18584/artifactory"
CREDS = json.loads((LAB / "credentials.json").read_text())
OUT.mkdir(exist_ok=True)
(OUT / "bodies").mkdir(exist_ok=True)
SESSION = requests.Session()
SESSION.trust_env = False

def redact(text):
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
    for value in strings(CREDS):
        if value:
            text = text.replace(value, "<secret-redacted>")
    return text

def req(label, method, path, who="admin", data=None, headers=None, timeout=90,
        auth_override=None):
    original = label
    retry = 1
    while (OUT / "bodies" / (label + ".txt")).exists():
        label = original + "-retry-" + str(retry)
        retry += 1
    trace = uuid.uuid4().hex[:16]
    hdr = {"User-Agent": "art-a-research/" + label,
           "X-JFrog-Trace-Id": trace, "X-Request-ID": label}
    hdr.update(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data)
        hdr.setdefault("Content-Type", "application/json")
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tic = time.perf_counter()
    try:
        response = SESSION.request(method, BASE + path, data=data, headers=hdr,
            auth=auth_override or ((who, CREDS[who]) if who != "anonymous" else None),
            timeout=timeout, allow_redirects=False)
        duration = time.perf_counter() - tic
        body = redact(response.content.decode("utf-8", errors="replace"))
        (OUT / "bodies" / (label + ".txt")).write_text(body)
        record = {
            "id": label, "start_utc": started, "end_utc": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            "identity": who, "method": method, "url": response.request.url,
            "path": response.request.path_url,
            "request_headers": {k: ("<redacted>" if k.lower() == "authorization"
                 else v) for k, v in response.request.headers.items()},
            "request_body": redact(data.decode() if isinstance(data, bytes) else
                                   data or ""),
            "status": response.status_code,
            "response_headers": dict(response.headers),
            "response_body_file": "bodies/" + label + ".txt",
            "response_bytes": len(response.content),
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
            "duration_s": duration, "trace_requested": trace,
        }
    except requests.RequestException as e:
        response = None
        record = {"id": label, "identity": who, "method": method, "path": path,
                  "start_utc": started, "error": redact(str(e))}
    with (OUT / "requests.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    print(label, who, method, path[:160],
          record.get("status", record.get("error")), flush=True)
    return response

def ok(r):
    if r is None or not 200 <= r.status_code < 300:
        raise RuntimeError("Request failed: " + (r.text[:500] if r is not None else "network"))
    return r

def permission(cache=("r", "w"), anon=("r",), exclude=""):
    actions = {"r": "read", "w": "upload", "n": "annotate", "d": "delete"}
    users = {"a-read": ["r"], "a-cache": list(cache),
             "a-cache2": ["r", "w"], "anonymous": list(anon)}
    model = {"name": "a-test", "repoPermission": {
        "repoKeys": ["github-remote"], "includePatterns": ["**"],
        "excludePatterns": [exclude] if exclude else [],
        "userPermissionActions": [
            {"principal": user, "actions": [actions[a] for a in grants]}
            for user, grants in users.items()],
        "groupPermissionActions": []}}
    return ok(req("permission-" + uuid.uuid4().hex[:8], "PUT",
        "/ui/permissiontargets/a-test", data=model))

def anonymous(enabled):
    r = ok(req("read-security-config-" + uuid.uuid4().hex[:6],
               "GET", "/ui/securityconfig"))
    config = r.json()
    config["anonAccessEnabled"] = enabled
    return ok(req("anonymous-" + str(enabled).lower() + "-" + uuid.uuid4().hex[:6],
        "PUT", "/ui/securityconfig", data=config))
