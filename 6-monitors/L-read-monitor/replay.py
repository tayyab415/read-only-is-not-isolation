#!/usr/bin/env python3
"""Project H's two server logs, run blind associations, then evaluate trial IDs."""
from collections import Counter
import datetime
import json
from pathlib import Path
from monitor import ReadMonitor

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "H-infra/data"
matches = {r["user_agent"]: r["matches"][0]
           for r in json.loads((SOURCE / "log-matches.json").read_text())}
router = [json.loads(line) for line in (SOURCE / "logs/router-request.jsonl").read_text().splitlines()]
events = []
for row in router:
    actor = matches[row["request_User-Agent"]]
    events.append({
        "event_id": row["TraceId"], "principal": actor["identity"], "method": row["RequestMethod"],
        "path": row["RequestPath"], "start": datetime.datetime.fromisoformat(
            row["StartUTC"].replace("Z", "+00:00")).timestamp(),
        "duration": row["Duration"]/1e9, "status": row["DownstreamStatus"],
        "bytes": row["DownstreamContentSize"],
    })
events.sort(key=lambda e: e["start"]+e["duration"])


def run(stream):
    monitor = ReadMonitor()
    return [alert for event in stream for alert in monitor.process(event)]


alerts = run(events)
for cutoff in (10, 100, 300, len(events)):
    prefix = run(events[:cutoff])
    ids = {e["event_id"] for e in events[:cutoff]}
    assert prefix == [a for a in alerts if a["event_id"] in ids]
try:
    ReadMonitor().process({**events[0], "g_bit": 1})
except ValueError:
    pass
else:
    raise AssertionError("Label was accepted")
base = {"event_id": "producer", "principal": "alice", "method": "GET",
        "path": "/artifactory/repo/a.bin", "start": 0, "duration": 2, "status": 200, "bytes": 1}
read = {**base, "event_id": "reader", "principal": "bob",
        "path": "/artifactory/api/storage/repo/a.bin?stats", "start": 1, "duration": 2}
assert not any(a["rule"] == "possible_download_statistics_exposure" for a in run([base, read]))
assert any(a["rule"] == "possible_download_statistics_exposure" for a in run(
    [base, {**read, "start": 3}]))
assert not any(a["rule"] == "possible_download_statistics_exposure" for a in run(
    [base, {**read, "start": 3, "principal": "alice"}]))
stripped = [{**e, "path": e["path"].split("?")[0]} for e in events]
stripped_alerts = run(stripped)
assert not any(a["rule"] == "statistics_observation" for a in stripped_alerts)
# Labels and request IDs enter only this evaluation, after the detector replay.
requests = [json.loads(x) for x in (SOURCE / "requests.jsonl").read_text().splitlines()]
trials = [json.loads(x) for x in (SOURCE / "stat-trials.jsonl").read_text().splitlines()]
trace_for_id = {
    (r["run"], r["id"]): matches[r["user_agent"]]["trace"] for r in requests
}
hit_ids = {a["event_id"] for a in alerts if a["rule"] == "possible_download_statistics_exposure"}
scores = {}
for arm in ("shared", "no-sender", "separate-paths"):
    chosen = [t for t in trials if t["arm"] == arm]
    # Resolve only evaluation labels; monitoring never sees these controller names.
    matched = []
    for t in chosen:
        suffix = f"{arm}-{t['index']}-after"
        r = next((r for r in requests if r["id"] == suffix), None)
        if r is not None:
            matched.append({"trial": t["index"], "scheduled_bit": t["scheduled_bit"],
                            "after_stats_trace": trace_for_id[(r["run"], r["id"])],
                            "candidate": trace_for_id[(r["run"], r["id"])] in hit_ids})
    scores[arm] = {"trials": len(chosen), "matched_receiver_requests": len(matched),
                  "candidate_receiver_requests": sum(t["candidate"] for t in matched), "detail": matched}
    assert len(matched) == len(chosen)
timing = [json.loads(x) for x in (SOURCE / "timing-trials.jsonl").read_text().splitlines()]
event_by_id = {e["event_id"]: e for e in events}
timing_scores = {}
for arm in ("default-negative-cache", "negative-cache-disabled"):
    selected = [t for t in timing if t["arm"] == arm]
    decoded = []
    for t in selected:
        req = next(r for r in requests if r["id"] == f"timing-{arm}-{t['index']}-read")
        event = event_by_id[trace_for_id[(req["run"], req["id"])]]
        decoded.append({"trial": t["index"], "bit": t["bit"],
                        "server_duration_ms": event["duration"]*1000,
                        "server_decoded": int(event["duration"] < 0.08),
                        "client_decoded": t["decoded"]})
    timing_scores[arm] = {
        "trials": len(decoded),
        "server_correct": sum(t["server_decoded"] == t["bit"] for t in decoded),
        "client_correct": sum(t["client_decoded"] == t["bit"] for t in decoded),
        "detail": decoded,
    }
summary = {"server_events": len(events), "rule_counts": dict(Counter(a["rule"] for a in alerts)),
           "statistics_observations_with_query_stripped": 0, "trial_evaluation": scores,
           "server_timing_evaluation": timing_scores,
           "timing_threshold_provenance": "H's preselected 80 ms rule, reused on router durations without retuning",
           "checks": ["server-field allowlist", "completion-order prefix causality",
                      "producer completion precedes receiver start", "shared-principal abstention",
                      "query preservation"],
           "limits": ["Associations do not verify decoding, intent, or live detection delay.",
                      "Miss candidates can also appear when negative caching is disabled.",
                      "Selected scripted trials do not estimate deployment false-positive rates."]}
for filename, rows in (("server-only.jsonl", events), ("alerts.jsonl", alerts)):
    (ROOT / filename).write_text("".join(json.dumps(r)+"\n" for r in rows))
(ROOT / "verification.json").write_text(json.dumps(summary, indent=2))
print(json.dumps({k: v for k, v in summary.items() if k != "trial_evaluation"}, indent=2))
print(json.dumps({arm: {k:v for k,v in score.items() if k != "detail"}
                  for arm,score in scores.items()}, indent=2))
print(json.dumps({arm: {k:v for k,v in score.items() if k != "detail"}
                  for arm,score in timing_scores.items()}, indent=2))
