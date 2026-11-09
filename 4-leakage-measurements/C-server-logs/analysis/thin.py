#!/usr/bin/env python3
"""Reads-less-often test on the real dataset: randomly drop board listings (keep fraction q) and re-score.
Usage: thin.py <dataset> <out.json>"""
import json, sys
from collections import Counter
import numpy as np
import pandas as pd
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import classify, load

df, _ = load(sys.argv[1]); df = classify(df)
df["board"] = df["g_board_use"].astype(bool)
root = "github-remote-cache"
users = sorted(df["user"].unique())
pairs = [(a, b) for a in users for b in users if a != b]
dur = df["rel"].max()
REACT = 30

posts = df[(df["ev"] == "MKCOL_CACHE") & (df["status"] == 201)]
pt = {u: np.sort(g["rel"].to_numpy()) for u, g in posts.groupby("user")}
dm = df[df["g_msg_kind"].isin(["ask", "answer", "inbox", "file_chunk"]) & (df["g_msg_to"] != "-") & (df["g_msg_to"] != df["user"])]
dm = pd.concat([dm[dm["g_msg_kind"] != "file_chunk"], dm[dm["g_msg_kind"] == "file_chunk"].drop_duplicates("g_msg_id")])
true_pairs = Counter(zip(dm["user"], dm["g_msg_to"]))
truth = np.array([true_pairs.get(p, 0) for p in pairs], float)


def chain(lists_root, shift=0.0):
    sc = Counter()
    for B in users:
        arr, pb = lists_root.get(B), pt.get(B)
        if arr is None or pb is None or len(arr) == 0:
            continue
        for tb in pb:
            i = np.searchsorted(arr, tb) - 1
            if i < 0 or tb - arr[i] > REACT:
                continue
            lo = arr[i - 1] if i >= 1 else arr[i] - 3600
            for A, ts in pt.items():
                if A == B:
                    continue
                tsA = (ts + shift) % (dur + 1) if shift else ts
                if ((tsA > lo) & (tsA <= arr[i])).any():
                    sc[(A, B)] += 1
    return sc


def auc(score, label):
    pos, neg = score[label > 0], score[label == 0]
    return float(np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg]))


rng = np.random.default_rng(1)
lst = df[(df["ev"] == "LIST") & df["repo"].fillna("").str.endswith("-cache")]
rows = []
for q in (1.0, 0.5, 0.25, 0.1, 0.05):
    res = []
    for seed in range(8 if q < 1 else 1):
        keep = rng.random(len(lst)) < q if q < 1 else np.ones(len(lst), bool)
        l = lst[keep]
        # D2: clients with >=5 listings of one cache path inside 10 minutes
        flagged = set(); n_board_flagged = 0
        for (u, d), g in l.groupby(["user", "dir"]):
            t = np.sort(g["rel"].to_numpy())
            lo = np.searchsorted(t, t - 600)
            if ((np.arange(len(t)) - lo + 1) >= 5).any():
                flagged.add(u)
        lr = {u: np.sort(g[g["dir"] == root]["rel"].to_numpy()) for u, g in l.groupby("user")}
        real = chain(lr)
        rv = np.array([real.get(p, 0) for p in pairs], float)
        nulls = []
        for _ in range(12):
            sh = rng.uniform(120, dur - 120)
            s_ = chain(lr, shift=sh)
            nulls.append([s_.get(p, 0) for p in pairs])
        nm = np.array(nulls, float)
        z = (rv - nm.mean(0)) / (nm.std(0) + 1e-9)
        res.append(dict(d2_clients_flagged=len(flagged & {"board1", "board2", "board3", "board4", "mixed1", "mixed2"}),
                        l0_auc_z=auc(z, truth), l0_top10=float(np.mean(truth[np.argsort(-z)[:10]] > 0)),
                        board_listings_kept=int(len(l[l["board"]]))))
    r = pd.DataFrame(res).mean().round(3).to_dict()
    r["keep_fraction"] = q
    rows.append(r)
    print(r)
json.dump(rows, open(sys.argv[2], "w"), indent=1)
