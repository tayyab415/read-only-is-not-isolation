#!/usr/bin/env python3
"""Provision only A's database and restart only art-a."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

lab = Path.home() / "workspaces/artifactory-lab/a"
credentials = lab / "credentials.json"
if not credentials.exists():
    data = {
        "admin": secrets.token_urlsafe(28),
        "a-read": secrets.token_urlsafe(28),
        "a-cache": secrets.token_urlsafe(28),
        "a-cache2": secrets.token_urlsafe(28),
        "postgres": secrets.token_urlsafe(28),
    }
    fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
data = json.loads(credentials.read_text())

def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout

run("docker", "stop", "art-a")
(lab / "evidence/first-start-failure.log").write_text(
    run("sudo", "cat", str(lab / "var/log/artifactory-service.log")))
run("docker", "rm", "art-a")
run("docker", "network", "create", "art-a-net")
run("docker", "pull", "postgres:16-alpine")
run("docker", "run", "-d", "--name", "art-a-db", "--network", "art-a-net",
    "--memory=512m", "--restart", "unless-stopped",
    "-e", "POSTGRES_USER=artifactory", "-e", "POSTGRES_DB=artifactory",
    "-e", "POSTGRES_PASSWORD=" + data["postgres"],
    "-v", str(lab / "db") + ":/var/lib/postgresql/data", "postgres:16-alpine")
config = f"""configVersion: 1
shared:
  extraJavaOpts: "-Xms512m -Xmx1536m"
  database:
    type: postgresql
    driver: org.postgresql.Driver
    url: jdbc:postgresql://art-a-db:5432/artifactory
    username: artifactory
    password: "{data['postgres']}"
"""
subprocess.run(["sudo", "tee", str(lab / "var/etc/system.yaml")],
               input=config, text=True, stdout=subprocess.DEVNULL, check=True)
run("sudo", "chmod", "600", str(lab / "var/etc/system.yaml"))
run("sudo", "chown", "1030:1030", str(lab / "var/etc/system.yaml"))
security_dir = lab / "var/etc/security"
run("sudo", "mkdir", "-p", str(security_dir))
for filename in ("master.key", "join.key"):
    target = security_dir / filename
    if subprocess.run(["sudo", "test", "-f", str(target)]).returncode != 0:
        subprocess.run(["sudo", "tee", str(target)], input=secrets.token_hex(32),
                       text=True, stdout=subprocess.DEVNULL, check=True)
        run("sudo", "chmod", "600", str(target))
        run("sudo", "chown", "1030:1030", str(target))
time.sleep(3)
run("docker", "run", "-d", "--name", "art-a", "--network", "art-a-net",
    "--memory=4g", "--memory-swap=4g", "--restart", "unless-stopped",
    "-p", "127.0.0.1:18583:8081", "-p", "127.0.0.1:18584:8082",
    "-v", str(lab / "var") + ":/var/opt/jfrog/artifactory",
    "-e", "JF_SHARED_EXTRAJAVAOPTS=-Xms512m -Xmx1536m",
    "releases-docker.jfrog.io/jfrog/artifactory-oss:7.161.26")
(lab / "evidence/container-hostconfig.json").write_text(
    run("docker", "inspect", "art-a", "--format", "{{json .HostConfig}}"))
print("PostgreSQL configured; art-a restarted. Credentials remain on labhost.")
