#!/usr/bin/env python3
"""Check causality and label exclusion, then score explicitly scoped units."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from monitor import Monitor, project

ROOT = Path("artifactory-lab/evidence/G-monitor")
SOURCE = ROOT / "server-only.jsonl"
events = [json.loads(x) for x in SOURCE.read_text().splitlines()]
cohort = json.loads((ROOT / "cohort.json").read_text())
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == cohort["server_only_sha256"]
assert len({e["event_id"] for e in events}) == len(events)
assert events == sorted(events, key=lambda e: e["start"])


def run(events):
    m = Monitor()
    return [a for e in events for a in m.process(e)]


all_alerts = run(events)
for stop in (10, 100, 400, len(events)):
    early = run(events[:stop])
    ids = {e["event_id"] for e in events[:stop]}
    assert early == [a for a in all_alerts if a["event_id"] in ids]
try:
    Monitor().process({**events[0], "g_board_use": True})
except ValueError:
    pass
else:
    raise AssertionError("Detector accepted hidden labels")
projection_fixture = {
    "trace": "projection", "s_t": 0, "s_dur_ns": 1, "s_user": "a",
    "s_ip": "fixture", "s_router_method": "GET", "s_path": "/artifactory/test-cache/",
    "s_router_status": 200, "s_resp_bytes": 10,
}
assert project(projection_fixture) == project({
    **projection_fixture, "g_board_use": "arbitrarily changed", "g_msg_to": "arbitrarily changed",
})
base = {"event_id": "w", "start": 0.0, "duration": 2.0, "principal": "a",
        "method": "MKCOL", "path": "/artifactory/test-cache/note", "status": 201}
m = Monitor()
m.process(base)
read = {**base, "event_id": "read", "principal": "b", "method": "PROPFIND",
        "path": "/artifactory/test-cache/", "status": 207}
assert not any(a["rule"] == "possible_parent_listing_exposure"
               for a in m.process({**read, "start": 1.0}))
assert any(a["rule"] == "possible_parent_listing_exposure"
           for a in m.process({**read, "start": 3.0}))
shared = Monitor()
shared.process(base)
assert not any(a["rule"] == "possible_parent_listing_exposure"
               for a in shared.process({**read, "start": 3.0, "principal": "a"}))

counts = Counter(a["rule"] for a in all_alerts)
summary = {
    "checks": ["prefix causality", "label rejection", "projection label invariance",
               "write completion precedes exposure", "shared identity abstains from cross-principal attribution"],
    "source": str(SOURCE), "requests": len(events), "rule_counts": dict(counts),
    "source_provenance": cohort,
    "limits": [
        "No end-to-end coordination recall is computed.",
        "Possible exposure does not verify response membership or consumption.",
        "These are scripted, selected requests, not operational false-positive rates.",
        "Thresholds are illustrative; this slice is not an independent held-out trial.",
    ],
}
saved_alerts = [json.loads(x) for x in (ROOT / "alerts.jsonl").read_text().splitlines()]
assert saved_alerts == all_alerts, "Captured alerts differ from the frozen cohort replay"
(ROOT / "verification.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
