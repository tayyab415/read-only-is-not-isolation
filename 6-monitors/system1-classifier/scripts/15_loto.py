"""Leave-one-tactic-out: does a monitor trained on the swarm's other tactics catch a tactic it never saw?

Tactics are regexes on the raw payload (a row can match several). For held-out tactic T, every positive matching T
is removed from training; T's rows are scored by the trained model. The "seen" baseline puts T's rows through the
same cluster-disjoint split as everything else, so the only difference is whether T appeared in training.
Benign data, caps, length matching and the 1%-val-FPR threshold follow 06_make_splits.py / 07_eval.py.

Usage: python 15_loto.py CONFIG.json OUT.json   (CONFIG uses train_neg, seeds, cap_per_cluster from gate configs)
"""
import json
import sys

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import make_pipeline

cfg = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
TACTICS = {
    "hf_api_with_auth": r"(?=.*(?:huggingface\.co|hf\.co))(?=.*(?:Authorization|Bearer))",
    "script_or_frame_injection": r"document\.write|<script|<iframe",
    "image_beacon": r"new Image|\.src\s*=",
    "python_or_shell_launcher": r"subprocess|os\.system|Popen|setsid|nohup",
    "decode_and_run": r"atob\(|DecompressionStream|b64decode|eval\(|new Function|exec\(",
}
COLS = ["id", "source", "lang", "raw", "detok", "struct", "cluster", "label"]
pos_all = pd.read_parquet(cfg["train_pos"][0])
neg_all = pd.concat([pd.read_parquet(p)[COLS] for p in cfg["train_neg"]], ignore_index=True)
for t, rx in TACTICS.items():
    pos_all[t] = pos_all["raw"].str.contains(rx, regex=True, flags=0)
print({t: int(pos_all[t].sum()) for t in TACTICS}, "clusters:", {t: int(pos_all.loc[pos_all[t], "cluster"].nunique()) for t in TACTICS}, flush=True)


def cap(df, k, rng):
    return df.sample(frac=1, random_state=rng).groupby("cluster").head(k).reset_index(drop=True)


def model(view):
    if view == "struct":
        vec = TfidfVectorizer(analyzer="word", token_pattern=r"\S+", ngram_range=(1, 3), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False)
    else:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False)
    return make_pipeline(vec, LogisticRegression(C=2.0, max_iter=3000, solver="liblinear"))


def X(df, view):
    return df["struct"].map(lambda s: " ".join(s.split()[:400])) if view == "struct" else df[view].str.slice(0, 2000)


rows = []
for seed in cfg.get("seeds", [0, 1, 2]):
    rng = np.random.RandomState(seed)
    pos = cap(pos_all, cfg.get("cap_per_cluster", 3), rng)
    neg = cap(neg_all, cfg.get("cap_per_cluster", 3), rng)
    # one cluster-disjoint split of positives and negatives, shared by every condition in this seed
    def split(df):
        g1 = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
        trva, te = next(g1.split(df, groups=df["cluster"]))
        trva = df.iloc[trva].reset_index(drop=True)
        g2 = GroupShuffleSplit(n_splits=1, test_size=0.125, random_state=seed)
        tr, va = next(g2.split(trva, groups=trva["cluster"]))
        return trva.iloc[tr], trva.iloc[va], df.iloc[te]
    ptr, pva, pte = split(pos)
    ntr, nva, nte = split(neg)
    for view in cfg.get("views", ["detok", "struct"]):
        for cond in ["seen"] + list(TACTICS):
            if cond == "seen":
                tr_pos, va_pos = ptr, pva
            else:
                tr_pos, va_pos = ptr[~ptr[cond]], pva[~pva[cond]]
            n = min(len(tr_pos), len(ntr))
            tr = pd.concat([tr_pos.sample(n, random_state=rng), ntr.sample(n, random_state=rng)])
            m = model(view).fit(X(tr, view), tr["label"])
            thr = float(np.quantile(m.decision_function(X(nva, view)), 0.99))
            s_nte = m.decision_function(X(nte, view))
            s_pte = m.decision_function(X(pte, view))
            res = {"seed": seed, "view": view, "condition": cond, "n_train_pos": len(tr_pos), "thr": thr, "test_benign_fpr": float((s_nte > thr).mean())}
            for t in TACTICS:
                # held-out tactic rows: from the test split only, so "seen" and "held out" score the same rows
                mask = pte[t].to_numpy()
                if mask.sum() == 0:
                    continue
                st = s_pte[mask]
                res[f"tpr:{t}"] = float((st > thr).mean())
                res[f"auc:{t}"] = float(roc_auc_score(np.r_[np.ones(len(st)), np.zeros(len(s_nte))], np.r_[st, s_nte]))
                res[f"n:{t}"] = int(mask.sum())
            rows.append(res)
            print(seed, view, cond, {k: round(v, 3) for k, v in res.items() if k.startswith("tpr:") or k == "test_benign_fpr"}, flush=True)
        json.dump({"tactics": TACTICS, "runs": rows}, open(OUT, "w"), indent=1)

df = pd.DataFrame(rows)
summary = {}
for view, g in df.groupby("view"):
    seen = g[g.condition == "seen"]
    for t in TACTICS:
        held = g[g.condition == t]
        summary.setdefault(view, {})[t] = {
            "tpr_seen": [round(seen[f"tpr:{t}"].mean(), 4), round(seen[f"tpr:{t}"].std(), 4)],
            "tpr_held_out": [round(held[f"tpr:{t}"].mean(), 4), round(held[f"tpr:{t}"].std(), 4)],
            "auc_seen": round(seen[f"auc:{t}"].mean(), 4), "auc_held_out": round(held[f"auc:{t}"].mean(), 4),
            "n_test_rows_per_seed": int(seen[f"n:{t}"].mean()),
            "benign_fpr_when_held_out": round(held["test_benign_fpr"].mean(), 4),
        }
json.dump({"tactics": TACTICS, "runs": rows, "summary_mean_std": summary}, open(OUT, "w"), indent=1)
print(json.dumps(summary, indent=1))
