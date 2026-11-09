"""Fit baselines on prepared splits, load encoder scores, compute one metric table for all models.

Thresholds come from validation only: thr1 = 99th percentile of val benign scores (1% val FPR), thr01 = 99.9th.
Rates carry Wilson 95% intervals. Results aggregate over seeds (mean, std, min, max).

Usage: python 07_eval.py SPLIT_DIR OUT.json [--encoder-scores DIR ...] [--views raw detok struct] [--skip-baselines]
  encoder score dirs look like DIR/seed{S}/scores_{val,test,eval_*}.parquet  (id, source, label, score)
"""
import argparse
import glob
import json
import os
import re

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from canon import PLACEHOLDER, SWARM_TOKEN

ap = argparse.ArgumentParser()
ap.add_argument("split_dir")
ap.add_argument("out")
ap.add_argument("--encoder-scores", nargs="*", default=[])
ap.add_argument("--views", nargs="*", default=["raw", "detok", "struct"])
ap.add_argument("--skip-baselines", action="store_true")
ap.add_argument("--dump-scores", default=None, help="dir to write baseline score parquets")
a = ap.parse_args()
MAX_CHARS, MAX_TOK = 2000, 400


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def view_text(df, v):
    if v == "struct":
        return df["struct"].map(lambda s: " ".join(s.split()[:MAX_TOK]))
    return df[v].str.slice(0, MAX_CHARS)


def text_model(v):
    if v == "struct":
        vec = TfidfVectorizer(analyzer="word", token_pattern=r"\S+", ngram_range=(1, 3), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False)
    else:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False)
    return make_pipeline(vec, LogisticRegression(C=2.0, max_iter=3000, solver="liblinear"))


def style_X(df):
    raw = df["raw"].str.slice(0, MAX_CHARS)
    n = raw.str.len().clip(lower=1)
    return np.c_[np.log1p(df["raw"].str.len()), raw.str.count("\n") / n, raw.str.count(r"\s") / n, raw.str.count(r"[^\w\s]") / n, raw.str.count(r"[A-Z]") / n, np.log1p(df["struct"].str.split().str.len())]


def token_X(df):
    return np.c_[np.log1p(df["raw"].map(lambda t: len(SWARM_TOKEN.findall(t)))), np.log1p(df["detok"].str.count(PLACEHOLDER))]


def metrics(scores):
    """scores: dict split_name -> DataFrame(label, source, score)."""
    va, te = scores["val"], scores["test"]
    vneg = va.loc[va.label == 0, "score"].to_numpy()
    thr1, thr01 = float(np.quantile(vneg, 0.99)), float(np.quantile(vneg, 0.999))
    m = {"thr1": thr1}
    y, s = te["label"].to_numpy(), te["score"].to_numpy()
    m["test_auc"] = float(roc_auc_score(y, s))
    m["test_ap"] = float(average_precision_score(y, s))
    for tag, thr in (("1", thr1), ("01", thr01)):
        k_tp, n_p = int((s[y == 1] > thr).sum()), int((y == 1).sum())
        k_fp, n_n = int((s[y == 0] > thr).sum()), int((y == 0).sum())
        m[f"test_tpr@thr{tag}"], m[f"test_tpr@thr{tag}_ci"] = k_tp / n_p, wilson(k_tp, n_p)
        m[f"test_fpr@thr{tag}"], m[f"test_fpr@thr{tag}_ci"] = k_fp / n_n, wilson(k_fp, n_n)
    for src, g in te[te.label == 0].groupby("source"):
        k = int((g.score > thr1).sum())
        m[f"test_fpr@thr1:{src}"] = k / len(g)
    tneg = te.loc[te.label == 0, "score"].to_numpy()
    evs = {k[5:]: v for k, v in scores.items() if k.startswith("eval_")}
    benign = {k: v for k, v in evs.items() if (v.label == 0).all()}
    attack = {k: v for k, v in evs.items() if (v.label == 1).all()}
    for k, v in benign.items():
        kk = int((v.score > thr1).sum())
        m[f"eval_fpr@thr1:{k}"], m[f"eval_fpr@thr1_ci:{k}"] = kk / len(v), wilson(kk, len(v))
    for k, v in attack.items():
        kk = int((v.score > thr1).sum())
        m[f"eval_tpr@thr1:{k}"], m[f"eval_tpr@thr1_ci:{k}"] = kk / len(v), wilson(kk, len(v))
        yy = np.r_[np.ones(len(v)), np.zeros(len(tneg))]
        m[f"eval_auc:{k}_vs_test_benign"] = float(roc_auc_score(yy, np.r_[v.score, tneg]))
        for kb, vb in benign.items():
            yy = np.r_[np.ones(len(v)), np.zeros(len(vb))]
            m[f"eval_auc:{k}_vs_{kb}"] = float(roc_auc_score(yy, np.r_[v.score, vb.score]))
    return m


seed_dirs = sorted(glob.glob(f"{a.split_dir}/splits/seed*"))
eval_sets = {os.path.basename(p)[:-8]: pd.read_parquet(p) for p in sorted(glob.glob(f"{a.split_dir}/evals/*.parquet"))}
rows, top = [], {}

if not a.skip_baselines:
    for sd in seed_dirs:
        seed = int(re.search(r"seed(\d+)", sd).group(1))
        tr, va, te = (pd.read_parquet(f"{sd}/{s}.parquet") for s in ("train", "val", "test"))
        sets = {"val": va, "test": te} | {f"eval_{k}": v for k, v in eval_sets.items()}
        models = {f"tfidf:{v}": ("text", v) for v in a.views} | {"probe:style": ("style", None), "probe:tokens": ("tokens", None)}
        for name, (kind, v) in models.items():
            if kind == "text":
                model, fx = text_model(v), (lambda df, v=v: view_text(df, v))
            else:
                model, fx = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)), (style_X if kind == "style" else token_X)
            model.fit(fx(tr), tr["label"])
            scores = {k: pd.DataFrame({"label": df["label"].to_numpy(), "source": df["source"].to_numpy(), "score": model.decision_function(fx(df))}) for k, df in sets.items()}
            if a.dump_scores:
                for k, df in scores.items():
                    os.makedirs(f"{a.dump_scores}/{name.replace(':', '-')}/seed{seed}", exist_ok=True)
                    df.assign(id=sets[k]["id"].astype(str).to_numpy()).to_parquet(f"{a.dump_scores}/{name.replace(':', '-')}/seed{seed}/scores_{k}.parquet", index=False)
            rows.append({"model": name, "seed": seed, **metrics(scores)})
            print(name, seed, {k: round(v, 4) for k, v in rows[-1].items() if isinstance(v, float) and ("auc" in k or "fpr@thr1:" in k or "tpr@thr1" in k)}, flush=True)
            if kind == "text" and seed == 0:
                vec, lr = model.steps[0][1], model.steps[1][1]
                names, coef = np.array(vec.get_feature_names_out()), lr.coef_[0]
                o = np.argsort(coef)
                top[name] = {"attack": [[names[i], round(float(coef[i]), 3)] for i in o[::-1][:30]], "benign": [[names[i], round(float(coef[i]), 3)] for i in o[:30]]}

for enc in a.encoder_scores:
    name = "encoder:" + os.path.basename(enc.rstrip("/"))
    for sd in sorted(glob.glob(f"{enc}/seed*")):
        seed = int(re.search(r"seed(\d+)", sd).group(1))
        scores = {os.path.basename(p)[7:-8]: pd.read_parquet(p) for p in glob.glob(f"{sd}/scores_*.parquet")}
        rows.append({"model": name, "seed": seed, **metrics(scores)})
        print(name, seed, {k: round(v, 4) for k, v in rows[-1].items() if isinstance(v, float) and ("auc" in k or "fpr@thr1:" in k or "tpr@thr1" in k)}, flush=True)

df = pd.DataFrame(rows)
num = [c for c in df.columns if c not in ("model", "seed") and df[c].map(lambda x: isinstance(x, float)).all()]
agg = df.groupby("model")[num].agg(["mean", "std", "min", "max"])
summary = {m: {c: [round(float(agg.loc[m, (c, s)]), 4) for s in ("mean", "std", "min", "max")] for c in num} for m in agg.index}
json.dump({"split_dir": a.split_dir, "runs": rows, "summary_mean_std_min_max": summary, "top_features_seed0": top}, open(a.out, "w"), indent=1, default=str)
print(df.groupby("model")[num].mean().T.round(4).to_string())
