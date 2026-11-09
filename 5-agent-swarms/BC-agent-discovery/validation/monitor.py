#!/usr/bin/env python3
"""Collection monitor: counts by study/status, failures, spend, elapsed. Read-only."""
import json, sys, collections
from pathlib import Path
BC = Path(__file__).resolve().parents[1]
data = BC / (sys.argv[1] if len(sys.argv) > 1 else "data")
eps = []
for f in sorted(data.glob("[BC]/*/episode.json")):
    e = json.loads(f.read_text()); c = json.loads((f.parent / "check.json").read_text())
    eps.append((e, c))
by = collections.Counter((e["study"], e["status"]) for e, _ in eps)
c_succ = sum(1 for e, c in eps if e["study"] == "C" and c.get("episode_success"))
c_actor = sum(1 for e, c in eps if e["study"] == "C" for a in "AB" if (c.get(a) or {}).get("complete"))
b_rec = sum(1 for e, c in eps if e["study"] == "B" and c.get("recovered"))
b_ans = sum(1 for e, c in eps if e["study"] == "B" and c.get("receiver_value") is not None)
walls = [e["wall_s"] for e, _ in eps if e.get("wall_s")]
led = {}
for l in (BC / "ledger" / "hf-ledger.jsonl").read_text().splitlines():
    r = json.loads(l); led[r["call_key"]] = r["usd"]
prog = [json.loads(l) for l in (data / "progress.jsonl").read_text().splitlines()] if (data / "progress.jsonl").exists() else []
ended = any(p.get("event") == "fleet_end" for p in prog)
bad = [(e["episode_id"], e["status"]) for e, _ in eps if e["status"] != "complete"]
print(json.dumps({"episodes": len(eps), "by_study_status": {f"{k[0]}:{k[1]}": v for k, v in by.items()},
                  "C_episode_success": c_succ, "C_actor_success": c_actor, "B_answered": b_ans, "B_recovered": b_rec,
                  "mean_wall_s": round(sum(walls) / len(walls), 1) if walls else None,
                  "hf_ledger_usd": round(sum(led.values()), 4), "non_complete": bad, "fleet_ended": ended}))
