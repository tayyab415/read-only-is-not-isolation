#!/usr/bin/env python3
"""Property-channel extension: what the logs show for property writes and property-search reads.
Usage: props_eval.py <dataset-dir> <out.json>"""
import json, sys
import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import classify, load, pr

df, _ = load(sys.argv[1])
df = classify(df)
df["board"] = df["g_board_use"].astype(bool)
R = {}
mat = df["rpath"].str.contains(";") & (df["method"] == "PUT")
cache = df["repo"].fillna("").str.endswith("-cache")
ok = df["status"] == 201
for name, sel in {"PUT with matrix properties, any outcome": mat, "PUT with matrix properties, 201 only": mat & ok,
                  "PUT with matrix properties into a *-cache repo": mat & cache}.items():
    tp = int((sel & df["board"]).sum()); fp = int((sel & ~df["board"]).sum()); fn = int((df["board"] & mat).sum()) - tp
    p, r = pr(tp, fp, fn)
    R[name] = dict(flagged=int(sel.sum()), tp=tp, fp=fp, fn=fn, precision=None if np.isnan(p) else round(p, 3), recall=None if np.isnan(r) else round(r, 3))
search = df[df["rpath"].str.contains("/api/search/prop")]
R["property_search_requests"] = search.groupby("user").size().to_dict()
rows = {}
for u, g in search.groupby("user"):
    t = np.sort(g["rel"].to_numpy())
    dt = np.diff(t)
    rows[u] = dict(n=len(g), median_gap_s=round(float(np.median(dt)), 1) if len(dt) else None,
                   cv=round(float(dt.std() / dt.mean()), 3) if len(dt) > 1 else None,
                   max_in_10min=int(max(((t >= x) & (t < x + 600)).sum() for x in t)) if len(t) else 0,
                   board=bool(g["board"].any()))
R["property_search_series"] = rows
# link: writer PUT -> reader's first property search afterwards
w = df[mat & ok & df["board"]]
rd = search[search["board"]]
gaps = []
for t in w["t"]:
    nxt = rd[rd["t"] > t]["t"]
    gaps.append(float(nxt.min() - t) if len(nxt) else np.nan)
R["write_to_first_board_search_gap_s"] = dict(n=len(gaps), median=round(float(np.nanmedian(gaps)), 1) if gaps else None,
                                               max=round(float(np.nanmax(gaps)), 1) if gaps else None)
R["listing_requests_by_board_clients"] = int(((df["ev"] == "LIST") & df["board"]).sum())
R["status_by_method"] = {f"{m}:{s}": int(n) for (m, s), n in df.groupby(["method", "status"]).size().items()}
json.dump(R, open(sys.argv[2], "w"), indent=1)
print(json.dumps(R, indent=1))
