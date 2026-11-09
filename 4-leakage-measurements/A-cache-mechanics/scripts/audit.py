#!/usr/bin/env python3
"""Verify body bytes, normalize initial decoding, and check for private secrets."""
import hashlib
import json
from pathlib import Path
import subprocess
from common import CREDS, LAB, OUT, req

if not (OUT / "bodies/old-default-password-denied.txt").exists():
    req("old-default-password-denied", "GET", "/api/system/version",
        who="admin-old-default", auth_override=("admin", "password"))
records = [json.loads(s) for s in (OUT / "requests.jsonl").read_text().splitlines()]
corrections = []
mismatches = []
for record in records:
    if "response_body_file" not in record:
        continue
    f = OUT / record["response_body_file"]
    text = f.read_text()
    if hashlib.sha256(text.encode()).hexdigest() == record["response_sha256"]:
        continue
    corrected = False
    for encoding in ["cp1252", "latin-1"]:
        try:
            raw = text.encode(encoding)
            if hashlib.sha256(raw).hexdigest() == record["response_sha256"]:
                f.write_bytes(raw)
                corrections.append({"id": record["id"], "recovered_from": encoding})
                corrected = True
                break
        except UnicodeEncodeError:
            pass
    if not corrected:
        mismatches.append(record["id"])
(OUT / "response-byte-audit.json").write_text(json.dumps({
    "records": len(records), "encoding_corrections": corrections,
    "unmatched_body_hashes": mismatches}, indent=2))
summary = json.loads((OUT / "bulk-summary.json").read_text())
by_id = {r["id"]: r for r in records}
for listing in summary["listings"]:
    if "duration_s" in listing:
        listing["time_to_headers_s"] = listing.pop("duration_s")
    listing["full_response_duration_s"] = by_id[listing["id"]]["duration_s"]
(OUT / "bulk-summary.json").write_text(json.dumps(summary, indent=2))
failures = []
secret_values = [(k, v) for k, v in CREDS.items() if isinstance(v, str) and len(v) > 16]
for keyfile in ["var/etc/security/master.key", "var/etc/security/join.key"]:
    value = subprocess.check_output(["sudo", "cat", str(LAB / keyfile)]).strip()
    secret_values.append((keyfile, value.decode()))
for path in OUT.rglob("*"):
    if path.is_file():
        text = path.read_text(errors="replace")
        for key, value in secret_values:
            if value in text:
                failures.append({"file": str(path.relative_to(OUT)), "secret_key_name": key})
(OUT / "secret-audit.json").write_text(json.dumps({
    "private_value_matches": failures, "credentials_copied": False}, indent=2))
if failures:
    raise RuntimeError("Private values found; do not copy evidence.")
print("Audit complete;", len(corrections), "encoding corrections;",
      len(mismatches), "body hash mismatches; no credential values found.")
