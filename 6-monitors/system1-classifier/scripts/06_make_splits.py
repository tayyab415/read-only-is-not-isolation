"""Make group-disjoint train/val/test splits per seed, plus held-out eval sets.

Config JSON:
  {"train_pos": [...parquet], "train_neg": [...parquet], "evals": {"name": {"path": ..., "label": 0|1}},
   "seeds": [0,1,2], "cap_per_cluster": 3, "max_per_class": 20000, "length_match": true,
   "fractions": [0.7, 0.1, 0.2], "eval_cap_per_cluster": 3, "eval_max": 5000}

Writes OUT_DIR/splits/seed{S}/{train,val,test}.parquet, OUT_DIR/evals/{name}.parquet, OUT_DIR/manifest.json
Usage: python 06_make_splits.py CONFIG.json OUT_DIR
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

cfg = json.load(open(sys.argv[1]))
out = sys.argv[2]
COLS = ["id", "source", "lang", "raw", "detok", "struct", "cluster", "label"]


def load(paths):
    paths = [paths] if isinstance(paths, str) else paths
    return pd.concat([pd.read_parquet(p)[COLS] for p in paths], ignore_index=True)


def cap(df, k, rng):
    return df.sample(frac=1, random_state=rng).groupby("cluster").head(k).reset_index(drop=True)


def length_matched(neg, pos, n, rng):
    pb = np.floor(np.log2(pos["raw"].str.len().clip(lower=1))).astype(int)
    nb = np.floor(np.log2(neg["raw"].str.len().clip(lower=1))).astype(int)
    want = (pb.value_counts(normalize=True) * n).round().astype(int)
    parts = [neg[nb == b].sample(min(w, int((nb == b).sum())), random_state=rng) for b, w in want.items()]
    got = pd.concat(parts)
    if len(got) < n:
        rest = neg.drop(got.index)
        got = pd.concat([got, rest.sample(min(n - len(got), len(rest)), random_state=rng)])
    return got.reset_index(drop=True)


pos_all, neg_all = load(cfg["train_pos"]), load(cfg["train_neg"])
manifest = {"config": cfg, "seeds": {}, "evals": {}}
fr = cfg.get("fractions", [0.7, 0.1, 0.2])

for seed in cfg.get("seeds", [0, 1, 2]):
    rng = np.random.RandomState(seed)
    pos = cap(pos_all, cfg.get("cap_per_cluster", 3), rng)
    neg = cap(neg_all, cfg.get("cap_per_cluster", 3), rng)
    n = min(len(pos), len(neg), cfg.get("max_per_class", 20000))
    pos = pos.sample(n, random_state=rng).reset_index(drop=True)
    neg = length_matched(neg, pos, n, rng) if cfg.get("length_match", True) else neg.sample(n, random_state=rng).reset_index(drop=True)
    data = pd.concat([pos, neg], ignore_index=True)
    g1 = GroupShuffleSplit(n_splits=1, test_size=fr[2], random_state=seed)
    trva_idx, te_idx = next(g1.split(data, groups=data["cluster"]))
    trva = data.iloc[trva_idx].reset_index(drop=True)
    g2 = GroupShuffleSplit(n_splits=1, test_size=fr[1] / (fr[0] + fr[1]), random_state=seed)
    tr_idx, va_idx = next(g2.split(trva, groups=trva["cluster"]))
    parts = {"train": trva.iloc[tr_idx], "val": trva.iloc[va_idx], "test": data.iloc[te_idx]}
    d = f"{out}/splits/seed{seed}"
    os.makedirs(d, exist_ok=True)
    info = {}
    for name, df in parts.items():
        df = df.sample(frac=1, random_state=rng).reset_index(drop=True)
        df.to_parquet(f"{d}/{name}.parquet", index=False)
        info[name] = {"n": len(df), "by_label": df["label"].value_counts().to_dict(), "by_source": df["source"].value_counts().to_dict()}
    assert not (set(parts["train"]["cluster"]) & set(parts["test"]["cluster"])), "cluster leak train/test"
    assert not (set(parts["train"]["cluster"]) & set(parts["val"]["cluster"])), "cluster leak train/val"
    manifest["seeds"][seed] = info
    print(seed, json.dumps(info), flush=True)

os.makedirs(f"{out}/evals", exist_ok=True)
rng = np.random.RandomState(1234)
for name, spec in cfg.get("evals", {}).items():
    df = load(spec["path"])
    df["label"] = spec["label"]
    df = cap(df, cfg.get("eval_cap_per_cluster", 3), rng)
    df = df.sample(min(len(df), cfg.get("eval_max", 5000)), random_state=rng).reset_index(drop=True)
    df.to_parquet(f"{out}/evals/{name}.parquet", index=False)
    manifest["evals"][name] = {"n": len(df), "label": spec["label"], "by_source": df["source"].value_counts().to_dict()}
    print("eval", name, len(df), flush=True)

json.dump(manifest, open(f"{out}/manifest.json", "w"), indent=1, default=str)
