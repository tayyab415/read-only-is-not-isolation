"""Do monitors trained on Swarm Traces flag natural benign tool calls from OpenAI models more than from other families?

Fits the v1 monitors (TF-IDF detok, TF-IDF struct, style probe) per seed on SPLIT_DIR/splits/seed*/train, sets the
1%-val-FPR threshold, and scores natural tool-call sets restricted to task index < MAX_TASK so every model answers the
same tasks. Reports FPR with Wilson 95% CIs per seed, mean over seeds, and median code length per model.

Usage: python 24_natural_eval.py SPLIT_DIR OUT.json MAX_TASK NAME=PARQUET:FAMILY [...]
"""
import glob
import json
import re
import sys

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

split_dir, out, max_task = sys.argv[1], sys.argv[2], int(sys.argv[3])
sets = {}
for spec in sys.argv[4:]:
    name, rest = spec.split("=", 1)
    path, fam = rest.rsplit(":", 1)
    d = pd.read_parquet(path)
    d = d[d["id"].astype(str).map(lambda s: int(s.rsplit(":", 1)[1]) < max_task)]
    sets[name] = (d.reset_index(drop=True), fam)
    js = d[d['lang'] == 'js']
    if len(js) >= 20:
        sets[name + '|js'] = (js.reset_index(drop=True), fam + '|js')  # language-matched comparison: the swarm is 94% JS


def wilson(k, n, z=1.96):
    p = k / n; dn = 1 + z * z / n; c = (p + z * z / (2 * n)) / dn
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dn
    return [round(c - h, 4), round(c + h, 4)]


def style_X(df):
    raw = df["raw"].str.slice(0, 2000); n = raw.str.len().clip(lower=1)
    return np.c_[np.log1p(df["raw"].str.len()), raw.str.count("\n") / n, raw.str.count(r"\s") / n, raw.str.count(r"[^\w\s]") / n, raw.str.count(r"[A-Z]") / n, np.log1p(df["struct"].str.split().str.len())]


MODELS = {
    "tfidf:detok": (lambda: make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False), LogisticRegression(C=2.0, max_iter=3000, solver="liblinear")), lambda df: df["detok"].str.slice(0, 2000)),
    "tfidf:struct": (lambda: make_pipeline(TfidfVectorizer(analyzer="word", token_pattern=r"\S+", ngram_range=(1, 3), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False), LogisticRegression(C=2.0, max_iter=3000, solver="liblinear")), lambda df: df["struct"].map(lambda s: " ".join(s.split()[:400]))),
    "probe:style": (lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)), style_X),
}
rows = []
for sd in sorted(glob.glob(f"{split_dir}/splits/seed*")):
    seed = int(re.search(r"seed(\d+)", sd).group(1))
    tr, va = pd.read_parquet(f"{sd}/train.parquet"), pd.read_parquet(f"{sd}/val.parquet")
    for mname, (mk, fx) in MODELS.items():
        m = mk().fit(fx(tr), tr["label"])
        thr = float(np.quantile(m.decision_function(fx(va[va.label == 0])), 0.99))
        for name, (d, fam) in sets.items():
            f = m.decision_function(fx(d)) > thr
            rows.append({"seed": seed, "model": mname, "set": name, "family": fam, "n": len(d), "flagged": int(f.sum()), "fpr": float(f.mean()), "ci95": wilson(int(f.sum()), len(d))})
            print(seed, mname, name, f"{100*f.mean():.1f}%", flush=True)
df = pd.DataFrame(rows)
summary = df.groupby(["model", "set", "family"]).agg(fpr_mean=("fpr", "mean"), fpr_std=("fpr", "std"), n=("n", "first")).round(4).reset_index()
lengths = {name: {"median_chars": int(d["raw"].str.len().median()), "n": len(d), "by_lang": d["lang"].value_counts().to_dict()} for name, (d, fam) in sets.items()}
fam_means = df.groupby(["model", "family", "seed"])["fpr"].mean().groupby(["model", "family"]).agg(["mean", "std"]).round(4).reset_index()
json.dump({"max_task": max_task, "runs": rows, "summary": summary.to_dict(orient="records"), "family_means": fam_means.to_dict(orient="records"), "lengths": lengths}, open(out, "w"), indent=1)
print(summary.to_string()); print(fam_means.to_string()); print(json.dumps(lengths, indent=1))
