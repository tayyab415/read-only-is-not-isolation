#!/usr/bin/env python3
"""Per-client table: when does each rule first fire?  Usage: clientsummary.py <dataset> <out.json>"""
import json, sys
import numpy as np
import pandas as pd
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import classify, load

df, _ = load(sys.argv[1]); df = classify(df)
df["board"] = df["g_board_use"].astype(bool)
cache = df["repo"].fillna("").str.endswith("-cache")


def first_k_in_window(t, k, W):
    t = np.sort(t)
    for i in range(k - 1, len(t)):
        if t[i] - t[i - k + 1] <= W:
            return float(t[i])
    return None


def first_periodic(t, n=8, cv=0.5):
    t = np.sort(t)
    for i in range(n - 1, len(t)):
        dt = np.diff(t[i - n + 1:i + 1])
        if dt.std() / dt.mean() < cv:
            return float(t[i])
    return None


out = []
for u, g in df.groupby("user"):
    row = dict(client=u, role=g["g_role"].iloc[0], requests=len(g), board_requests=int(g["board"].sum()))
    mk = g[(g["ev"] == "MKCOL_CACHE") & (g["status"] == 201)]["rel"].to_numpy()
    row["d1_first_mkcol_cache_min"] = round(mk.min() / 60, 1) if len(mk) else None
    t = first_k_in_window(mk, 3, 600)
    row["d1_3_in_10min_min"] = round(t / 60, 1) if t is not None else None
    best_rep, best_per = None, None
    for d, gg in g[(g["ev"] == "LIST") & cache.loc[g.index]].groupby("dir"):
        tt = first_k_in_window(gg["rel"].to_numpy(), 5, 600)
        if tt is not None and (best_rep is None or tt < best_rep):
            best_rep = tt
        tp = first_periodic(gg["rel"].to_numpy())
        if tp is not None and (best_per is None or tp < best_per):
            best_per = tp
    row["d2_5_same_path_in_10min_min"] = round(best_rep / 60, 1) if best_rep is not None else None
    row["d3_periodic_8_listings_min"] = round(best_per / 60, 1) if best_per is not None else None
    out.append(row)
json.dump(out, open(sys.argv[2], "w"), indent=1)
print(pd.DataFrame(out).to_string())
