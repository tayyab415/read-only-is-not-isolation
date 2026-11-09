"""Unseen-family false-positive rate as a function of how many model families' benign agent scripts are in training.

For each held-out family f and each subset S of the other families (|S| = 0..3): train TF-IDF + LR (detok) on swarm
positives vs v1 negatives + all rows of families in S (minus 15% of their clusters kept for validation). Threshold =
99th percentile of validation benign. Report FPR on every row of f, in-distribution TPR/FPR, and paired AUC on the
outside datasets. Splits of positives and v1 negatives are cluster-disjoint and fixed per seed.

Usage: python 21_family_curve.py CONFIG.json OUT.json  (CONFIG: gate_v1-style train_pos/train_neg/evals + "families": {name: path})
"""
import itertools
import json
import sys

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import make_pipeline

cfg = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
COLS = ["id", "source", "lang", "raw", "detok", "struct", "cluster", "label"]
pos_all = pd.read_parquet(cfg["train_pos"][0])[COLS]
neg_all = pd.concat([pd.read_parquet(p)[COLS] for p in cfg["train_neg"]], ignore_index=True)
fams = {k: pd.read_parquet(v)[COLS] for k, v in cfg["families"].items()}
evals = {k: pd.read_parquet(v["path"])[COLS] for k, v in cfg["evals"].items()}
extra = {k: pd.read_parquet(v)[COLS] for k, v in cfg.get("extra", {}).items()}  # always held out, scored on every fit
PAIRS = {"agentapprove": ("A_agentapprove", "B_agentapprove"), "tomngdev": ("A_tomng_deny", "B_tomng_allow"), "rogue": ("A_rogue_unsafe", "B_rogue_safe")}


def cap(df, k, rng):
    return df.sample(frac=1, random_state=rng).groupby("cluster").head(k).reset_index(drop=True)


def split(df, seed, test=0.2, val=0.125):
    g1 = GroupShuffleSplit(n_splits=1, test_size=test, random_state=seed)
    trva, te = next(g1.split(df, groups=df["cluster"]))
    trva = df.iloc[trva].reset_index(drop=True)
    g2 = GroupShuffleSplit(n_splits=1, test_size=val, random_state=seed)
    tr, va = next(g2.split(trva, groups=trva["cluster"]))
    return trva.iloc[tr], trva.iloc[va], df.iloc[te]


def X(df):
    return df["detok"].str.slice(0, 2000)


def run(seed, held, S):
    rng = np.random.RandomState(seed)
    pos = cap(pos_all, 3, rng)
    neg = cap(neg_all, 3, rng)
    ptr, pva, pte = split(pos, seed)
    ntr, nva, nte = split(neg, seed)
    add_tr, add_va = [], []
    for f in S:
        d = cap(fams[f], 3, rng)
        g = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=seed)
        a, b = next(g.split(d, groups=d["cluster"]))
        add_tr.append(d.iloc[a]); add_va.append(d.iloc[b])
    ntr2 = pd.concat([ntr] + add_tr, ignore_index=True)
    nva2 = pd.concat([nva] + add_va, ignore_index=True)
    n = min(len(ptr), len(ntr2))
    tr = pd.concat([ptr.sample(n, random_state=rng), ntr2.sample(n, random_state=rng) if len(ntr2) > n else ntr2])
    m = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False),
                      LogisticRegression(C=2.0, max_iter=3000, solver="liblinear")).fit(X(tr), tr["label"])
    thr = float(np.quantile(m.decision_function(X(nva2)), 0.99))
    s_held = m.decision_function(X(fams[held]))
    s_pte, s_nte = m.decision_function(X(pte)), m.decision_function(X(nte))
    r = {"seed": seed, "held_out_family": held, "train_families": sorted(S), "k": len(S), "held_fpr": float((s_held > thr).mean()),
         "test_tpr": float((s_pte > thr).mean()), "test_fpr": float((s_nte > thr).mean()), "n_held": len(s_held)}
    for name, e in extra.items():
        r[f"extra_fpr:{name}"] = float((m.decision_function(X(e)) > thr).mean())
    for p, (a, b) in PAIRS.items():
        sa, sb = m.decision_function(X(evals[a])), m.decision_function(X(evals[b]))
        r[f"paired_auc:{p}"] = float(roc_auc_score(np.r_[np.ones(len(sa)), np.zeros(len(sb))], np.r_[sa, sb]))
    return r


jobs = []
for seed in cfg.get("seeds", [0, 1, 2]):
    for held in fams:
        others = [f for f in fams if f != held]
        for k in range(len(others) + 1):
            for S in itertools.combinations(others, k):
                jobs.append((seed, held, S))
print(len(jobs), "fits", flush=True)
rows = Parallel(n_jobs=cfg.get("n_jobs", 3), verbose=5)(delayed(run)(*j) for j in jobs)
df = pd.DataFrame(rows)
by_k = df.groupby("k").agg(held_fpr_mean=("held_fpr", "mean"), held_fpr_std=("held_fpr", "std"), test_tpr=("test_tpr", "mean"), test_fpr=("test_fpr", "mean"),
                           **{f"paired_{p}": (f"paired_auc:{p}", "mean") for p in PAIRS}, **{f"extra_{n}": (f"extra_fpr:{n}", "mean") for n in extra}).round(4)
by_k_family = df.groupby(["held_out_family", "k"])["held_fpr"].agg(["mean", "std"]).round(4)
json.dump({"runs": rows, "by_k": by_k.reset_index().to_dict(orient="records"), "by_family_k": by_k_family.reset_index().to_dict(orient="records")}, open(OUT, "w"), indent=1)
print(by_k.to_string())
print(by_k_family.unstack("k").to_string())
