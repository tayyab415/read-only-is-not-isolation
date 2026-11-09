#!/usr/bin/env python3
"""Check H's exported evidence without displaying private credential values."""
import base64
import json
from pathlib import Path
import stat

ROOT = Path.home() / "workspaces/artifactory-lab"
OUT = ROOT / "h/evidence"
patterns = set()
credential_files = [ROOT / "a/credentials.json", ROOT / "h/credentials.json"]
for source in credential_files:
    credentials = json.loads(source.read_text())
    for user, value in credentials.items():
        if isinstance(value, str) and len(value) >= 10:
            patterns.add(value.encode())
            patterns.add(base64.b64encode(f"{user}:{value}".encode()))

files = [p for p in OUT.rglob("*") if p.is_file()
         and p.name != "redaction-verification.json"]
matches = []
for path in files:
    content = path.read_bytes()
    if any(secret in content for secret in patterns):
        matches.append(str(path.relative_to(OUT)))
result = {
    "scope": "H exported evidence; known A and H password values and Basic encodings",
    "files_checked": len(files),
    "secret_patterns_checked": len(patterns),
    "matching_files": matches,
    "h_private_credentials_mode": oct(stat.S_IMODE(credential_files[1].stat().st_mode)),
}
(OUT / "redaction-verification.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
assert not matches, "A known private credential appears in exported evidence"
assert result["h_private_credentials_mode"] == "0o600"
