"""Exploratory: decompose P2 receiver timed-probe latency (calibration only) into
client, gateway and Artifactory/router time, active vs idle sender.
Reads only calibration files. No holdout access."""
import json, statistics, sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "P2-residual/data/p2-m1"

def med(x): return statistics.median(x) if x else float("nan")

def load_router(lines):
    out = {}
    for line in lines:
        if line.startswith("{"):
            r = json.loads(line)
            m = r.get("request_User-Agent")
            if m: out[m] = r["Duration"] / 1e6
    return out

def mutable(arm):
    d = DATA / arm
    trials = [json.loads(l) for l in open(d / "calibration.jsonl")]
    gw = {g["marker"]: (g["end_utc_ns"] - g["start_utc_ns"]) / 1e6 for g in json.load(open(d / "calibration-server.json"))["gateway_log"] if g.get("marker")}
    router = load_router(open(d / "calibration-server-log-lines.txt").read().splitlines())
    rows = defaultdict(lambda: defaultdict(list))
    for t in trials:
        for r in t["post"]:
            if not r["probe"].startswith("post.timed."): continue
            k = (t["label"], "GET" if int(r["probe"][-2:]) % 2 == 0 else "HEAD")
            rows[k]["client"].append(r["elapsed_ms"])
            if r["marker"] in gw: rows[k]["gateway"].append(gw[r["marker"]])
            if r["marker"] in router: rows[k]["router"].append(router[r["marker"]])
    return rows

def show(arm, rows):
    print(f"== {arm}")
    for method in ("GET", "HEAD"):
        a, i = rows[(1, method)], rows[(0, method)]
        parts = []
        for comp in ("client", "gateway", "router"):
            if a.get(comp) and i.get(comp):
                parts.append(f"{comp}: active {med(a[comp]):.2f} / idle {med(i[comp]):.2f} ms (n={len(a[comp])}/{len(i[comp])})")
        print(f"  {method}: " + " | ".join(parts))

for arm in ("separate", "sham", "aa"):
    show(arm, mutable(arm))
