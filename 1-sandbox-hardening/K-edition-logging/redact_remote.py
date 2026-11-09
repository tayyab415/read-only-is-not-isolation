#!/usr/bin/env python3
"""Run on the owned K VM before exporting; report counts, never secret values."""
import base64
import hashlib
import json
from pathlib import Path

root = Path("/home/ubuntu/k-versions")
out = root / "evidence"
needles = set()
for path in root.glob("*-credentials.json"):
    for user, value in json.loads(path.read_text()).items():
        needles.add(value.encode())
        needles.add(base64.b64encode((user+":"+value).encode()))
for path in root.glob("*/var/etc/security/*.key"):
    value = path.read_bytes().strip()
    if value:
        needles.add(value)
changed = {}
files = [p for p in out.rglob("*") if p.is_file() and p.name != "redaction.json"]
for path in files:
    content = path.read_bytes()
    count = sum(content.count(value) for value in needles)
    if count:
        for value in needles:
            content = content.replace(value, b"<secret-redacted>")
        path.write_bytes(content)
        changed[str(path.relative_to(out))] = count
assert all(not any(value in p.read_bytes() for value in needles) for p in files)
report = {
    "scope": "generated passwords, Basic values, master and join keys",
    "files_scanned": len(files), "secret_values_checked": len(needles),
    "redacted_occurrences_by_file": changed, "remaining_known_secret_matches": 0,
    "artifact_sha256": {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in files},
}
(out / "redaction.json").write_text(json.dumps(report, indent=2))
print(json.dumps({k: v for k, v in report.items() if k != "artifact_sha256"}, indent=2))
