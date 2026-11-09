"""Gate: how much of the Swarm-vs-benign separation is artifact, and does any of it transfer?

For each seed: cap positives and negatives per near-duplicate cluster, balance classes (optionally
matching the length distribution), then StratifiedGroupKFold by cluster. For each view (raw, detok,
struct) fit TF-IDF + logistic regression, and two artifact probes (length/style, redaction-token count).

Per fold we report ROC-AUC, PR-AUC, TPR at 1% and 0.1% in-distribution FPR, and at the 1%-FPR
threshold: FPR on each benign eval set and TPR on each attack eval set. Threshold-free transfer:
AUC of each attack eval set against each benign eval set.

Usage: python 03_gate.py CONFIG.json OUT.json
"""
import json
import re
import sys
import time

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from canon import SWARM_TOKEN, PLACEHOLDER

cfg = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
MAX_CHARS = cfg.get("max_chars", 2000)
MAX_STRUCT_TOKENS = cfg.get("max_struct_tokens", 400)


def load(paths):
    paths = [paths] if isinstance(paths, str) else paths
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


def cap_per_cluster(df, k, rng):
    return df.sample(frac=1, random_state=rng).groupby("cluster").head(k).reset_index(drop=True)


def length_matched_sample(neg, pos, n, rng):
    """Sample n negatives whose log2-length histogram follows the positives' as closely as supply allows."""
    pb = np.floor(np.log2(pos["raw"].str.len().clip(lower=1))).astype(int)
    nb = np.floor(np.log2(neg["raw"].str.len().clip(lower=1))).astype(int)
    want = (pb.value_counts(normalize=True) * n).round().astype(int)
    parts = []
    for b, w in want.items():
        pool = neg[nb == b]
        parts.append(pool.sample(min(w, len(pool)), random_state=rng))
    out = pd.concat(parts)
    if len(out) < n:  # top up from whatever is left
        rest = neg.drop(out.index)
        out = pd.concat([out, rest.sample(min(n - len(out), len(rest)), random_state=rng)])
    return out.reset_index(drop=True)


def view_text(df, view):
    if view == "struct":
        return df["struct"].map(lambda s: " ".join(s.split()[:MAX_STRUCT_TOKENS]))
    return df[view].str.slice(0, MAX_CHARS)


def text_model(view):
    if view == "struct":
        vec = TfidfVectorizer(analyzer="word", token_pattern=r"\S+", ngram_range=(1, 3), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False)
    else:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False)
    return make_pipeline(vec, LogisticRegression(C=2.0, max_iter=3000, solver="liblinear"))


def style_features(df):
    raw = df["raw"].str.slice(0, MAX_CHARS)
    n = raw.str.len().clip(lower=1)
    return np.c_[
        np.log1p(df["raw"].str.len()),
        raw.str.count("\n") / n,
        raw.str.count(r"\s") / n,
        raw.str.count(r"[^\w\s]") / n,
        raw.str.count(r"[A-Z]") / n,
        np.log1p(df["struct"].str.split().str.len()),
    ]


def token_features(df):
    return np.c_[
        np.log1p(df["raw"].map(lambda t: len(SWARM_TOKEN.findall(t)))),
        np.log1p(df["detok"].str.count(PLACEHOLDER)),
    ]


def probe_model():
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))


def tpr_at_fpr(y, s, fpr_target):
    fpr, tpr, _ = roc_curve(y, s)
    return float(np.interp(fpr_target, fpr, tpr))


def fold_metrics(y, s, evals_scores):
    neg = s[y == 0]
    thr = float(np.quantile(neg, 0.99))
    m = {
        "auc": float(roc_auc_score(y, s)),
        "ap": float(average_precision_score(y, s)),
        "tpr@fpr1%": tpr_at_fpr(y, s, 0.01),
        "tpr@fpr0.1%": tpr_at_fpr(y, s, 0.001),
    }
    benign = {k: v for k, (lab, v) in evals_scores.items() if lab == 0}
    attack = {k: v for k, (lab, v) in evals_scores.items() if lab == 1}
    for k, v in benign.items():
        m[f"fpr@thr1%:{k}"] = float((v > thr).mean())
    for k, v in attack.items():
        m[f"tpr@thr1%:{k}"] = float((v > thr).mean())
        for kb, vb in benign.items():
            yy = np.r_[np.ones(len(v)), np.zeros(len(vb))]
            m[f"auc:{k}_vs_{kb}"] = float(roc_auc_score(yy, np.r_[v, vb]))
        # also against the in-distribution benign test fold
        yy = np.r_[np.ones(len(v)), np.zeros(len(neg))]
        m[f"auc:{k}_vs_infold_benign"] = float(roc_auc_score(yy, np.r_[v, neg]))
    return m


def top_features(model, k=30):
    vec, lr = model.steps[0][1], model.steps[1][1]
    names = np.array(vec.get_feature_names_out())
    coef = lr.coef_[0]
    order = np.argsort(coef)
    return {"attack": [(names[i], round(float(coef[i]), 3)) for i in order[::-1][:k]], "benign": [(names[i], round(float(coef[i]), 3)) for i in order[:k]]}


pos_all = load(cfg["train_pos"])
neg_all = load(cfg["train_neg"])
evals = {name: load(spec["path"]) for name, spec in cfg.get("evals", {}).items()}
eval_labels = {name: spec["label"] for name, spec in cfg.get("evals", {}).items()}
views = cfg.get("views", ["raw", "detok", "struct"])
results = {"config": cfg, "runs": [], "top_features": {}}
t0 = time.time()

for seed in cfg.get("seeds", [0]):
    rng = np.random.RandomState(seed)
    pos = cap_per_cluster(pos_all, cfg.get("cap_per_cluster", 3), rng)
    neg = cap_per_cluster(neg_all, cfg.get("cap_per_cluster", 3), rng)
    n = min(len(pos), len(neg), cfg.get("max_per_class", 20000))
    pos = pos.sample(n, random_state=rng).reset_index(drop=True)
    neg = length_matched_sample(neg, pos, n, rng) if cfg.get("length_match", True) else neg.sample(n, random_state=rng).reset_index(drop=True)
    data = pd.concat([pos, neg], ignore_index=True)
    y = data["label"].to_numpy()
    groups = data["cluster"].to_numpy()
    skf = StratifiedGroupKFold(n_splits=cfg.get("folds", 5), shuffle=True, random_state=seed)
    print(f"seed {seed}: {len(pos)} pos, {len(neg)} neg, neg sources {neg['source'].value_counts().to_dict()}", flush=True)

    models = {f"text:{v}": v for v in views}
    models["probe:style"] = "style"
    models["probe:tokens"] = "tokens"
    for fold, (tr, te) in enumerate(skf.split(data, y, groups)):
        for mname, v in models.items():
            if v == "style":
                Xtr, Xte, model = style_features(data.iloc[tr]), style_features(data.iloc[te]), probe_model()
                ev = {k: style_features(e) for k, e in evals.items()}
            elif v == "tokens":
                Xtr, Xte, model = token_features(data.iloc[tr]), token_features(data.iloc[te]), probe_model()
                ev = {k: token_features(e) for k, e in evals.items()}
            else:
                Xtr, Xte, model = view_text(data.iloc[tr], v), view_text(data.iloc[te], v), text_model(v)
                ev = {k: view_text(e, v) for k, e in evals.items()}
            model.fit(Xtr, y[tr])
            s = model.decision_function(Xte)
            es = {k: (eval_labels[k], model.decision_function(X)) for k, X in ev.items()}
            m = fold_metrics(y[te], s, es)
            results["runs"].append({"seed": seed, "fold": fold, "model": mname, **m})
            print(f"  seed {seed} fold {fold} {mname:14s} auc={m['auc']:.4f} tpr@1%={m['tpr@fpr1%']:.3f} ({time.time()-t0:.0f}s)", flush=True)
            if seed == cfg.get("seeds", [0])[0] and fold == 0 and mname.startswith("text:"):
                results["top_features"][mname] = top_features(model)
        json.dump(results, open(OUT, "w"), indent=1)

runs = pd.DataFrame(results["runs"])
metric_cols = [c for c in runs.columns if c not in ("seed", "fold", "model")]
summary = runs.groupby("model")[metric_cols].agg(["mean", "std", "min", "max"])
results["summary"] = {m: {c: {s: round(float(summary.loc[m, (c, s)]), 4) for s in ("mean", "std", "min", "max")} for c in metric_cols if not np.isnan(summary.loc[m, (c, "mean")])} for m in summary.index}
json.dump(results, open(OUT, "w"), indent=1)
print(runs.groupby("model")[metric_cols].mean().T.round(4).to_string())
