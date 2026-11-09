"""Exploratory, calibration only: Artifactory router duration of receiver timed GETs,
by sender recipe, and by probe offset (during vs after the sender window)."""
import json, statistics
from collections import defaultdict
from decompose import DATA, load_router, med

for arm in ("separate", "sham"):
    d = DATA / arm
    trials = [json.loads(l) for l in open(d / "calibration.jsonl")]
    router = load_router(open(d / "calibration-server-log-lines.txt").read().splitlines())
    rec = defaultdict(list); off = defaultdict(list)
    sender_reach = defaultdict(list)
    for t in trials:
        vals = [router[r["marker"]] for r in t["post"] if r["probe"].startswith("post.timed.") and r["marker"] in router]
        rec[(t["recipe"], t["label"])].append(med(vals))
        for r in t["post"]:
            if r["marker"] in router:
                off[(r["probe"], t["label"])].append(router[r["marker"]])
        if t["label"] == 1:
            sender_reach[t["recipe"]].append(sum(1 for s in t["sender_records"] if s["marker"] in router))
    print(f"== {arm}: router ms per trial (median of 15 timed probes), active / idle; sender requests reaching Artifactory per active trial")
    for r in sorted({k[0] for k in rec}):
        print(f"  {r:13s} {med(rec[(r,1)]):.2f} / {med(rec[(r,0)]):.2f}   reach {med(sender_reach[r]) if sender_reach[r] else 0:.0f}")
    if arm == "separate":
        print("  by probe (active / idle):")
        for p in sorted({k[0] for k in off}):
            print(f"    {p:20s} {med(off[(p,1)]):.2f} / {med(off[(p,0)]):.2f}")
