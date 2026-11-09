#!/usr/bin/env python3
"""Independently check J bodies/decoders and replay trusted gateway records."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from monitor import ReadMonitor

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "J-controls/data"

def load(name):
    return [json.loads(line) for line in (SOURCE / name).read_text().splitlines()]

requests = load("requests.jsonl")
by_label = {r["label"]: r for r in requests}
assert len(by_label) == len(requests)
for r in requests:
    body = (SOURCE / r["body_file"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == r["body_sha256"], r["label"]
joins = json.loads((SOURCE / "log-matches.json").read_text())
events, aliases = [], {}
for row in joins:
    if not row["gateway_matches"]:
        continue
    assert len(row["gateway_matches"]) == 1
    g = row["gateway_matches"][0]
    service = row["service_matches"]
    assert len(service) in (0, 1)
    if service:
        s = service[0]
        assert s["status"] == g["status"] and s["method"] == g["method"]
        assert s["path"] == g["path"].removeprefix("/artifactory").split("?")[0]
    else:
        assert g["policy_reason"] is not None
    # Full path and clocks are trusted gateway fields; authenticated identity
    # comes from the service trace. Blocked requests have no confirmed identity.
    events.append({"event_id": g["marker"], "principal": service[0]["identity"] if service else None,
        "method": g["method"], "path": g["path"], "start": g["start_utc_ns"]/1e9,
        "duration": (g["end_utc_ns"]-g["start_utc_ns"])/1e9,
        "status": g["status"], "bytes": g["body_bytes"]})

# Derive aliases only from administrator repository configuration readbacks.
for r in requests:
    if r["role"] == "admin" and r["method"] == "GET" and "/ui/admin/repositories/remote/" in r["path"] and r["status"] == 200:
        repo = r["path"].rsplit("/", 1)[1]
        config = json.loads((SOURCE / r["body_file"]).read_text())
        assert config["general"]["repoKey"] == repo
        aliases[repo+"-cache"] = repo
events.sort(key=lambda r: r["start"]+r["duration"])
monitor = ReadMonitor(repo_aliases=aliases)
alerts = [a for r in events for a in monitor.process(r)]
prefix_monitor = ReadMonitor(repo_aliases=aliases)
prefix = [a for r in events[:100] for a in prefix_monitor.process(r)]
ids = {r["event_id"] for r in events[:100]}
assert prefix == [a for a in alerts if a["event_id"] in ids]

timing, stats = load("timing-trials.jsonl"), load("stat-trials.jsonl")
schedule = json.loads((SOURCE / "preregister.json").read_text())
thresholds = json.loads((SOURCE / "frozen-thresholds.json").read_text())
timing_result = {}
for delay in (150, 20):
    for arm in sorted({t["arm"] for t in timing if t["phase"] == "holdout"}):
        rows = [t for t in timing if t["phase"] == "holdout" and t["delay_ms"] == delay and t["arm"] == arm]
        assert len(rows) == 32 and sum(t["expected_bit"] for t in rows) == 16
        assert [t["scheduled_bit"] for t in rows] == schedule["timing_bits"][str(delay)][arm]
        for t in rows:
            r = by_label[t["receiver_label"]]
            assert r["duration_ns"] == t["duration_ns"]
            assert t["decoded_bit"] == int(t["duration_ns"] < thresholds[str(delay)]["threshold_ns"])
        timing_result[f"{delay}/{arm}"] = {"correct": sum(t["decoded_bit"] == t["expected_bit"] for t in rows),
                                          "trials": len(rows), "decoded_ones": sum(t["decoded_bit"] for t in rows)}
statistics_result = {}
for arm in sorted({t["arm"] for t in stats}):
    rows = [t for t in stats if t["arm"] == arm]
    assert len(rows) == 24 and sum(t["expected_bit"] for t in rows) == 12
    assert [t["expected_bit"] for t in rows] == schedule["statistics_bits"][arm]
    for t in rows:
        bodies = [json.loads((SOURCE / by_label[t[label]]["body_file"]).read_text())
                  if t[status] == 200 else None for label, status in
                  (("before_label", "before_status"), ("after_label", "after_status"))]
        if all(b is not None for b in bodies):
            assert bodies[0]["downloadCount"] == t["before_count"]
            assert bodies[1]["downloadCount"] == t["after_count"]
            assert t["decoded_bit"] == int(t["after_count"] > t["before_count"])
        else:
            assert t["decoded_bit"] is None
    statistics_result[arm] = {"observed": sum(t["decoded_bit"] is not None for t in rows),
        "correct": sum(t["decoded_bit"] is not None and t["decoded_bit"] == t["expected_bit"] for t in rows),
        "trials": len(rows)}
packages = load("package-trials.jsonl")
assert len(packages) == 14 and all(p["pip_exit_code"] == 0 and p["package_function_passed"]
                                 and p["installed_version"] == "1.16.0" for p in packages)
assert len({p["download_sha256"] for p in packages}) == 1
source_names = ["requests.jsonl", "log-matches.json", "timing-trials.jsonl", "stat-trials.jsonl",
                "preregister.json", "frozen-thresholds.json", "package-trials.jsonl"]
result = {"source_sha256": {n: hashlib.sha256((SOURCE/n).read_bytes()).hexdigest() for n in source_names},
          "verified_response_bodies": len(requests), "gateway_events": len(events),
          "trusted_cache_aliases": aliases, "monitor_rule_counts": dict(Counter(a["rule"] for a in alerts)),
          "timing": timing_result, "statistics": statistics_result,
          "verified_package_installs": len(packages),
          "limits": ["Main controlled cohorts; no operational false-positive rate.",
                     "Repeated shared paths cause associations on later zero trials too.",
                     "Fast failures alone also occur on an offline service.",
                     "No live collector arrival or alert delivery time was measured."]}
for name, rows in (("controls-server-only.jsonl", events), ("controls-alerts.jsonl", alerts)):
    (ROOT / name).write_text("".join(json.dumps(r)+"\n" for r in rows))
(ROOT / "controls-verification.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
