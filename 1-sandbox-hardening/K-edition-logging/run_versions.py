#!/usr/bin/env python3
"""Run bounded ordinary API/log tests with a fresh database per edition."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path.home() / "k-versions"
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)
IMAGES = {
    "oss-7.133.6": "releases-docker.jfrog.io/jfrog/artifactory-oss:7.133.6",
    "jcr-7.133.6": "releases-docker.jfrog.io/jfrog/artifactory-jcr:7.133.6@sha256:55edcdeb55f92aba5ff223c526056db772e7d946669e74ceb27049750fc2c298",
}


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def inside_request(method, path, headers, data=None):
    # The isolated bridge has no host gateway. A trusted controller runs curl
    # inside the server container; private headers arrive over stdin.
    config = ["url = "+json.dumps("http://localhost:8082/artifactory"+path),
              "request = "+json.dumps(method), "silent", "show-error", "max-time = 30",
              "write-out = "+json.dumps("\n%{http_code}")]
    for key, value in headers.items():
        config.append("header = "+json.dumps(key+": "+value))
    if data is not None:
        config.append("data = "+json.dumps(data.decode()))
    p = subprocess.run(["docker", "exec", "-i", "k-art", "curl", "--config", "-"],
                       input="\n".join(config)+"\n", capture_output=True, text=True, timeout=40)
    assert p.returncode == 0, "In-container controller request failed"
    body, status = p.stdout.rsplit("\n", 1)
    return int(status), body.encode()


def study(name, image):
    dest = OUT / name
    dest.mkdir(exist_ok=True)
    creds = {u: secrets.token_urlsafe(24) for u in ("admin", "k-read", "k-cache", "postgres")}
    private = ROOT / (name + "-credentials.json")
    fd = os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(creds, stream)
    def redact(text):
        for value in creds.values():
            text = text.replace(value, "<secret-redacted>")
        return text

    def request(label, method, path, user="admin", body=None, default=False):
        password = "<DEFAULT_ADMIN_PASSWORD>" if default else creds[user]
        headers = {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
                   "User-Agent": "k-version/" + name + "/" + label}
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif body is not None:
            data = body.encode()
        else:
            data = None
        start, mono = time.time(), time.monotonic()
        status, payload = inside_request(method, path, headers, data)
        record = {"id": label, "identity": user, "method": method, "path": path,
                  "start_utc": start, "duration_s": time.monotonic()-mono,
                  "status": status, "user_agent": headers["User-Agent"],
                  "request_body": redact(data.decode()) if data else None,
                  "response_body": redact(payload.decode(errors="replace")),
                  "response_sha256": hashlib.sha256(payload).hexdigest(),
                  "controller_transport": "in-container curl over docker exec stdin"}
        with (dest / "requests.jsonl").open("a") as stream:
            stream.write(json.dumps(record)+"\n")
        print(name, label, status, flush=True)
        return status, record["response_body"]

    pull = run("docker", "pull", image, check=False)
    (dest / "pull.txt").write_text(pull.stdout + pull.stderr)
    if pull.returncode:
        return {"image": image, "outcome": "image_pull_failed"}
    (dest / "image.json").write_text(run("docker", "image", "inspect", image).stdout)
    var = ROOT / name / "var"
    (var / "etc/security").mkdir(parents=True)
    config = f"""configVersion: 1
shared:
  extraJavaOpts: "-Xms512m -Xmx1536m"
  database:
    type: postgresql
    driver: org.postgresql.Driver
    url: jdbc:postgresql://k-db:5432/artifactory
    username: artifactory
    password: "{creds['postgres']}"
"""
    (var / "etc/system.yaml").write_text(config)
    os.chmod(var / "etc/system.yaml", 0o600)
    for filename in ("master.key", "join.key"):
        p = var / "etc/security" / filename
        p.write_text(secrets.token_hex(32))
        os.chmod(p, 0o600)
    run("sudo", "chown", "-R", "1030:1030", str(var))
    run("docker", "network", "create", "--internal", "--opt",
        "com.docker.network.bridge.gateway_mode_ipv4=isolated", "k-net")
    run("docker", "run", "-d", "--name", "k-db", "--network", "k-net", "--memory", "512m",
        "-e", "POSTGRES_USER=artifactory", "-e", "POSTGRES_DB=artifactory",
        "-e", "POSTGRES_PASSWORD=" + creds["postgres"], "postgres:16-alpine")
    fixture = ROOT / "fixture.py"
    fixture.write_text("""from http.server import BaseHTTPRequestHandler,HTTPServer
class H(BaseHTTPRequestHandler):
 def do_HEAD(self): self.send_response(200); self.send_header('Content-Length','16'); self.end_headers()
 def do_GET(self):
  self.send_response(200); self.send_header('Content-Length','16'); self.end_headers(); self.wfile.write(b'harmless bytes\\n\\n')
HTTPServer(('0.0.0.0',8000),H).serve_forever()
""")
    run("docker", "run", "-d", "--name", "k-upstream", "--network", "k-net", "--memory", "96m",
        "-v", str(fixture)+":/fixture.py:ro", "python:3.12-slim", "python3", "/fixture.py")
    run("docker", "run", "-d", "--name", "k-art", "--network", "k-net", "--memory", "6g",
        "--memory-swap", "6g", "-p", "127.0.0.1:18584:8082",
        "-v", str(var)+":/var/opt/jfrog/artifactory",
        "-e", "JF_SHARED_EXTRAJAVAOPTS=-Xms512m -Xmx1536m", image)
    result = {"image": image, "outcome": "starting"}
    try:
        deadline = time.monotonic()+420
        while time.monotonic() < deadline:
            try:
                ping = run("docker", "exec", "k-art", "curl", "-fsS", "--max-time", "3",
                           "http://localhost:8082/artifactory/api/system/ping", check=False)
                if ping.returncode == 0 and ping.stdout.strip() == "OK":
                    break
            except Exception:
                pass
            time.sleep(5)
        else:
            result["outcome"] = "startup_timeout"
            return result
        if name.startswith("jcr"):
            request("eula", "POST", "/ui/jcr/eula/accept", default=True)
        status, _ = request("rotate-admin", "PUT", "/ui/users/admin", default=True, body={
            "name": "admin", "email": "k-admin@example.invalid", "password": creds["admin"],
            "retypedPassword": creds["admin"], "admin": True, "profileUpdatable": True})
        assert status == 200
        request("version", "GET", "/api/system/version")
        for user in ("k-read", "k-cache"):
            status, _ = request("create-"+user, "POST", "/ui/users", body={
                "name": user, "email": user+"@example.invalid", "password": creds[user],
                "admin": False, "groups": [], "profileUpdatable": False})
            assert status in (200, 201)
        repositories = []
        for kind in ("local", "remote"):
            config = {"type": kind+"RepoConfig", "general": {"repoKey": "k-"+kind},
                      "basic": {"includesPattern": "**/*", "layout": "simple-default"},
                      "advanced": {}, "typeSpecific": {"repoType": "Generic"}}
            if kind == "remote":
                config["basic"].update(url="http://k-upstream:8000", offline=False)
                config["advanced"]["storeArtifactsLocally"] = True
            status, _ = request("create-"+kind, "POST", "/ui/admin/repositories", body=config)
            if status in (200, 201):
                repositories.append("k-"+kind)
                request("readback-"+kind, "GET", "/ui/admin/repositories/"+kind+"/k-"+kind)
        assert repositories
        status, _ = request("grant", "POST", "/ui/permissiontargets", body={
            "name": "k-test", "repoPermission": {"repoKeys": repositories,
            "includePatterns": ["**"], "userPermissionActions": [
                {"principal": "k-read", "actions": ["read"]},
                {"principal": "k-cache", "actions": ["read", "upload"]}],
            "groupPermissionActions": []}})
        assert status in (200, 201)
        request("grant-readback", "GET", "/ui/permissiontargets/k-test")
        for repo in repositories:
            cached = repo+"-cache" if repo.endswith("remote") else repo
            if repo.endswith("local"):
                request(repo+"-seed", "PUT", "/"+repo+"/fixture.bin", body="harmless bytes\n\n")
            else:
                request(repo+"-seed", "GET", "/"+repo+"/fixture.bin", "k-cache")
            for user in ("k-read", "k-cache"):
                request(repo+"-mkcol-"+user, "MKCOL", "/"+cached+"/folder-"+user, user)
                request(repo+"-folder-info-"+user, "GET", "/api/storage/"+cached+"/folder-"+user)
            request(repo+"-list", "GET", "/api/storage/"+cached+"?list&deep=1")
            request(repo+"-stats", "GET", "/api/storage/"+cached+"/fixture.bin?stats", "k-read")
        result.update(outcome="completed", available_repositories=repositories)
        query = """select repo,node_path,node_name,node_type,created_by,modified_by
from nodes where repo like 'k-%' order by repo,node_path,node_name;"""
        db = run("docker", "exec", "k-db", "psql", "-U", "artifactory", "-d", "artifactory",
                 "-A", "-F", "|", "-c", query, check=False)
        (dest / "database-nodes.txt").write_text(db.stdout+db.stderr)
        return result
    finally:
        for container in ("k-art", "k-db", "k-upstream"):
            log = run("docker", "logs", container, check=False)
            (dest / (container+"-console.txt")).write_text(redact(log.stdout+log.stderr))
        for filename in ("artifactory-request.log", "artifactory-access.log",
                         "router-request.log", "access-audit.log", "artifactory-service.log"):
            source = var / "log" / filename
            content = run("sudo", "cat", str(source), check=False)
            if content.returncode == 0:
                (dest / filename).write_text(redact(content.stdout))
        run("docker", "rm", "-f", "k-art", "k-db", "k-upstream", check=False)
        run("docker", "network", "rm", "k-net", check=False)
        for f in dest.rglob("*"):
            if f.is_file():
                assert not any(v.encode() in f.read_bytes() for v in creds.values()), f.name


if __name__ == "__main__":
    run("docker", "pull", "postgres:16-alpine")
    run("docker", "pull", "python:3.12-slim")
    results = {}
    for original, image in IMAGES.items():
        name = original+"-routed" if "--routed" in sys.argv else original
        try:
            results[name] = study(name, image)
        except Exception as error:
            results[name] = {"outcome": "test_error", "error_type": type(error).__name__,
                             "error": str(error)[:200]}
        (OUT / "summary.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
