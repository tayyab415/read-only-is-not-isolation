#!/usr/bin/env python3
"""Collect only M's server markers, and check exports for known passwords."""
import base64
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "evidence"
logs = OUT / "logs"
logs.mkdir(exist_ok=True)
state = json.loads((OUT / "state.json").read_text())
for filename in ("artifactory-request.log", "router-request.log"):
    result = subprocess.run(["docker", "exec", "art-a", "cat",
                             "/var/opt/jfrog/artifactory/log/"+filename],
                            check=True, capture_output=True, text=True)
    selected = [line for line in result.stdout.splitlines() if "m-controller/"+state["run"]+"/" in line]
    (logs / filename).write_text("\n".join(selected)+"\n")
creds = json.loads((ROOT / "credentials.json").read_text())
admin = json.loads((ROOT.parent / "a/credentials.json").read_text())["admin"]
creds["admin"] = admin
patterns = [pattern for user, password in creds.items()
            for pattern in (password.encode(), base64.b64encode(f"{user}:{password}".encode()))]
files = [p for p in OUT.rglob("*") if p.is_file() and p.name != "redaction.json"]
matched = [str(p.relative_to(OUT)) for p in files if any(s in p.read_bytes() for s in patterns)]
record = {"files_checked": len(files), "known_password_and_basic_matches": matched}
(OUT / "redaction.json").write_text(json.dumps(record, indent=2))
assert not matched
print(json.dumps(record))
