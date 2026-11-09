#!/usr/bin/env python3
"""Observed serial schedule rates, including controller overhead; no capacity claim."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

def measure(requests, symbols, correct, observed):
    seconds = (max(r["end_utc_ns"] for r in requests)-min(r["start_utc_ns"] for r in requests))/1e9
    return {"scheduled_symbols": symbols, "observed_symbols": observed,
            "correct_symbols": correct, "wall_span_s": seconds,
            "scheduled_symbols_per_s": symbols/seconds, "http_requests": len(requests)}

h = ROOT.parent / "H-infra/data"
hr = read(h / "requests.jsonl")
ht = read(h / "stat-trials.jsonl")
j = ROOT.parent / "J-controls/data"
jr = {r["label"]: r for r in read(j / "requests.jsonl")}
result = {"H_statistics": {}, "J_statistics": {}, "J_timing": {}}
for arm in ("shared", "no-sender", "separate-paths"):
    t = [r for r in ht if r["arm"] == arm]
    r = [r for r in hr if r["id"].startswith(arm+"-") and
         r["id"].rsplit("-", 1)[-1] in ("before", "send", "after")]
    result["H_statistics"][arm] = measure(r, len(t), sum(x["decoded_bit"] == x["expected_bit"] for x in t), len(t))
for arm in sorted({r["arm"] for r in read(j / "stat-trials.jsonl")}):
    t = [r for r in read(j / "stat-trials.jsonl") if r["arm"] == arm]
    labels = [x[key] for x in t for key in ("before_label", "sender_label", "after_label") if x[key]]
    result["J_statistics"][arm] = measure([jr[l] for l in labels], len(t),
        sum(x["decoded_bit"] is not None and x["decoded_bit"] == x["expected_bit"] for x in t),
        sum(x["decoded_bit"] is not None for x in t))
for delay in (150, 20):
    for arm in sorted({r["arm"] for r in read(j / "timing-trials.jsonl") if r["phase"] == "holdout"}):
        t = [r for r in read(j / "timing-trials.jsonl") if r["phase"] == "holdout"
             and r["delay_ms"] == delay and r["arm"] == arm]
        labels = [x[key] for x in t for key in ("sender_label", "receiver_label") if x[key]]
        result["J_timing"][f"{delay}/{arm}"] = measure([jr[l] for l in labels], len(t),
            sum(x["decoded_bit"] == x["expected_bit"] for x in t), len(t))
result["interpretation"] = ("Serial preselected symbols per elapsed wall second, including synchronization and "
    "controller overhead. A rate on a blocked/chance arm is attempted symbols, not transmitted information. "
    "No maximum capacity or production throughput is estimated.")
(ROOT / "rates.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
