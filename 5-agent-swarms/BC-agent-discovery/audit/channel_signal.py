#!/usr/bin/env python3
"""Exploratory (not preregistered): was the load channel physically exercised?

For each verified complete episode, from the trusted gateway rows only:
- peer_inflight: number of the peer tenant's requests in flight at the moment each of this
  actor's requests was sent upstream (t_up), on the same node;
- this actor's service latency (t_down - t_up, ms) for each of its requests.
Per episode and direction we report the median latency when the peer had 0 requests in
flight vs at least 1, the ratio, and the Spearman correlation. Shared and independent
infrastructure are compared. In independent episodes the peer runs on another node, so the
in-flight count is taken on this actor's own node (always 0 if isolation holds).

B: direction sender -> receiver. C: both directions.
Usage: channel_signal.py data [--verification audit/verification.json] -> audit/channel_signal.json
"""
import argparse, json, statistics
from pathlib import Path

AUDIT = Path(__file__).resolve().parent


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 5 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = rank(x), rank(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def direction(rows, me, peer):
    mine = [r for r in rows if r.get("tenant") == me and r.get("status") == 200 and r.get("t_up") and r.get("t_down")]
    node = {r.get("node") for r in mine}
    theirs = [r for r in rows if r.get("tenant") == peer and r.get("t_up") and r.get("t_down") and r.get("node") in node]
    lat, infl = [], []
    for r in mine:
        t = r["t_up"]
        infl.append(sum(1 for q in theirs if q["t_up"] <= t < q["t_down"]))
        lat.append((r["t_down"] - r["t_up"]) / 1e6)
    idle = [l for l, n in zip(lat, infl) if n == 0]
    busy = [l for l, n in zip(lat, infl) if n > 0]
    out = {"n_requests": len(lat), "n_with_peer_inflight": len(busy), "peer_requests_same_node": len(theirs),
           "median_ms_peer_idle": round(statistics.median(idle), 2) if idle else None,
           "median_ms_peer_busy": round(statistics.median(busy), 2) if busy else None,
           "spearman_latency_vs_peer_inflight": spearman(infl, lat)}
    if out["median_ms_peer_idle"] and out["median_ms_peer_busy"]:
        out["busy_over_idle"] = round(out["median_ms_peer_busy"] / out["median_ms_peer_idle"], 3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", type=Path)
    ap.add_argument("--verification", type=Path, default=AUDIT / "verification.json")
    ap.add_argument("--out", type=Path, default=AUDIT / "channel_signal.json")
    a = ap.parse_args()
    ok = None
    if a.verification.exists():
        v = json.loads(a.verification.read_text())
        eps = v.get("episodes", v)
        ok = {k for k, r in eps.items() if isinstance(r, dict) and r.get("ok")}
    res = {}
    for f in sorted(a.data.glob("[BC]/*/episode.json")):
        e = json.loads(f.read_text())
        if e["status"] != "complete" or (ok is not None and e["episode_id"] not in ok):
            continue
        spec = json.loads((f.parent / "spec.json").read_text())
        rows = [json.loads(l) for l in (f.parent / "gateway.jsonl").read_text().splitlines() if l.strip()]
        ten = {x: spec["actors"][x]["tenant_id"] for x in ("A", "B")}
        cell = spec["schedule_row"]["cell"]
        dirs = {"A_to_B": direction(rows, ten["B"], ten["A"])}
        if e["study"] == "C":
            dirs["B_to_A"] = direction(rows, ten["A"], ten["B"])
        res[e["episode_id"]] = {"study": e["study"], "cell": cell, "directions": dirs}
    summary = {}
    for (study, infra) in sorted({(r["study"], r["cell"]["infra"]) for r in res.values()}):
        ds = [d for r in res.values() if r["study"] == study and r["cell"]["infra"] == infra for d in r["directions"].values()]
        ratios = [d["busy_over_idle"] for d in ds if d.get("busy_over_idle")]
        rhos = [d["spearman_latency_vs_peer_inflight"] for d in ds if d.get("spearman_latency_vs_peer_inflight") is not None]
        summary[f"{study}:{infra}"] = {
            "directions": len(ds), "with_any_peer_overlap": sum(1 for d in ds if d["n_with_peer_inflight"] > 0),
            "median_busy_over_idle": round(statistics.median(ratios), 3) if ratios else None,
            "median_spearman": round(statistics.median(rhos), 3) if rhos else None,
            "n_ratio": len(ratios), "n_rho": len(rhos)}
    a.out.write_text(json.dumps({"note": "exploratory, not preregistered; trusted gateway rows only",
                                 "summary": summary, "episodes": res}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
