#!/usr/bin/env python3
"""Verify controlled measurements against their server and fixture logs."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent / "data"
rows = [json.loads(x) for x in (ROOT / "requests.jsonl").read_text().splitlines()]
matches = json.loads((ROOT / "log-matches.json").read_text())
mapping = {r["user_agent"]: r["matches"] for r in matches}
routers = {
    r["request_User-Agent"]: r for r in
    (json.loads(x) for x in (ROOT / "logs/router-request.jsonl").read_text().splitlines())
}
assert len(mapping) == len(rows) == len(routers)
for r in rows:
    assert len(mapping[r["user_agent"]]) == 1, r["id"]
    s = mapping[r["user_agent"]][0]
    assert s["identity"] == r["identity"] and s["method"] == r["method"]
    assert s["status"] == r["status"], r["id"]
    assert s["path"] == urlsplit(r["path"]).path, r["id"]
    assert routers[r["user_agent"]]["DownstreamStatus"] == r["status"]
    assert routers[r["user_agent"]]["RequestMethod"] == r["method"]
    assert routers[r["user_agent"]]["RequestPath"] == "/artifactory" + r["path"], r["id"]
    body = (ROOT / r["response_body_file"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == r["response_sha256"], r["id"]

connectivity = json.loads((ROOT / "connectivity.json").read_text())
assert connectivity["receiver_listener_control"][-1]["result"] == "connected"
assert connectivity["target_listener_control"][-1]["result"] == "connected"
assert connectivity["sender"][0]["result"] == "connected"
assert all(r["result"] != "connected" for r in connectivity["sender"][1:])
cleanup = json.loads((ROOT / "cleanup.json").read_text())
assert cleanup["remote_repo_offline_status"] == 200
assert cleanup["compose_down_returncode"] == 0
assert all(r["returncode"] == 0 for r in cleanup["network_detaches"])
readback = json.loads((ROOT / "cleanup-readback.json").read_text())
assert readback["remote_offline"] is True
assert not readback["remaining_h_containers"] and not readback["remaining_h_networks"]
redaction = json.loads((ROOT / "redaction-verification.json").read_text())
assert not redaction["matching_files"]
assert redaction["h_private_credentials_mode"] == "0o600"

trials = [json.loads(x) for x in (ROOT / "stat-trials.jsonl").read_text().splitlines()]
spec = json.loads((ROOT / "stat-preregister.json").read_text())
shared = [r for r in trials if r["arm"] == "shared"]
assert [r["scheduled_bit"] for r in shared] == spec["bits"]
assert all(r["decoded_bit"] == r["expected_bit"] for r in shared)
assert all(r["before_count"] == 0 for r in trials)
assert all(r["after_count"] == 0 for r in trials if r["arm"] != "shared")
assert all(r["after_count"] in (0, 1) for r in shared)
assert len(trials) == 80

upstream = [json.loads(x) for x in (ROOT / "logs/h-upstream.jsonl").read_text().splitlines()]
target = [json.loads(x) for x in (ROOT / "logs/h-target.jsonl").read_text().splitlines()]
heads = Counter(r["path"] for r in upstream if r["method"] == "HEAD")
for i in range(12):
    assert heads[f"/missing/read-pair-{i}.bin"] == 1
timing = [json.loads(x) for x in (ROOT / "timing-trials.jsonl").read_text().splitlines()]
for t in timing:
    pathname = "/missing/" + t["path"].split("/missing/", 1)[1]
    expected = 1 if t["arm"] == "default-negative-cache" else 1 + t["bit"]
    assert heads[pathname] == expected, (pathname, heads[pathname], expected)
assert any(r["method"] == "HEAD" and r["path"] == "/final/fixture.bin" for r in target)

metrics = {
    "verified_requests": len(rows),
    "record_server_identity_status_matches": len(rows),
    "requests_with_query_omitted_from_artifactory_path_but_retained_by_router": sum(
        bool(urlsplit(r["path"]).query) for r in rows),
    "response_body_hashes_verified": len(rows),
    "direct_connectivity_controls": "passed",
    "helper_cleanup": "passed",
    "cleanup_readback": "passed",
    "known_credential_redaction_check": "passed",
    "stat_trials": len(trials),
    "stat_shared_bits_correct": sum(r["decoded_bit"] == r["scheduled_bit"] for r in shared),
    "stat_shared_bits": len(shared),
    "stat_no_sender_positive": sum(r["decoded_bit"] == 1 for r in trials if r["arm"] == "no-sender"),
    "stat_separate_path_positive": sum(r["decoded_bit"] == 1 for r in trials if r["arm"] == "separate-paths"),
    "read_only_missing_pairs_with_one_upstream_head": 12,
    "redirect_probe": {},
    "timing": {},
}
for label in ("redirect-read-only", "redirect-cache", "redirect-prewarmed-read"):
    r = next(r for r in rows if r["id"] == label)
    metrics["redirect_probe"][label] = {"status": r["status"],
                                      "body": (ROOT / r["response_body_file"]).read_text()}
for arm in ("default-negative-cache", "negative-cache-disabled"):
    arm_rows = [t for t in timing if t["arm"] == arm]
    metrics["timing"][arm] = {
        "bits": len(arm_rows),
        "correct": sum(t["decoded"] == t["bit"] for t in arm_rows),
        "decoded_one": sum(t["decoded"] for t in arm_rows),
        "by_bit": {},
    }
    for bit in (0, 1):
        selected = [t for t in arm_rows if t["bit"] == bit]
        server_ms = []
        for t in selected:
            rec = next(r for r in rows if r["id"] == f"timing-{arm}-{t['index']}-read")
            server_ms.append(routers[rec["user_agent"]]["Duration"] / 1e6)
        metrics["timing"][arm]["by_bit"][str(bit)] = {
            "n": len(selected),
            "client_median_ms": statistics.median(t["duration_ns"] / 1e6 for t in selected),
            "server_median_ms": statistics.median(server_ms),
        }
(ROOT / "verified-summary.json").write_text(json.dumps(metrics, indent=2))
print(json.dumps(metrics, indent=2))
