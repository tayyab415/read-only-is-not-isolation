"""Model-free per-call floor: how many swarm actions have a benign twin in real benign traffic?

A twin under a view means any monitor reading that view must score the two alike. We report, over the curated
Swarm Traces actions (row-weighted and cluster-weighted):
  struct_exact   identical struct form to >= 1 benign action
  struct_near    MinHash Jaccard >= 0.8 on struct 3-gram shingles
  detok_cos>=t   max character 3-5-gram TF-IDF cosine to the benign pool (t = 0.9, 0.8)
by attack type (regex on detok). The benign pool is real data only (no synthetic). Bigger pools only raise these
numbers, so they are lower bounds.

Usage: python 13_floor.py OUT.json BENIGN_PARQUET [BENIGN_PARQUET ...]
"""
import hashlib
import json
import sys

import numpy as np
import pandas as pd
from datasketch import MinHash, MinHashLSH
from sklearn.feature_extraction.text import TfidfVectorizer

from cluster import PERMS, _shingles

out, benign_paths = sys.argv[1], sys.argv[2:]
pos = pd.read_parquet("../data/positives.parquet")
ben = pd.concat([pd.read_parquet(p) for p in benign_paths], ignore_index=True)
ben = ben[ben.label == 0].drop_duplicates(subset=["raw"]).reset_index(drop=True)
print("swarm", len(pos), "benign pool", len(ben), ben.source.value_counts().to_dict(), flush=True)

TYPES = {
    "hf_api_with_auth": r"huggingface\.co|hf\.co|/api/(?:datasets|models|spaces|repos)",
    "script_or_frame_injection": r"document\.write|<script|<iframe",
    "image_beacon": r"new Image|\.src\s*=",
    "python_or_shell_launcher": r"subprocess|os\.system|Popen|exec\(|setsid|nohup",
    "decode_and_run": r"atob\(|DecompressionStream|b64decode|eval\(|new Function",
}


def classify(t):
    for k, rx in TYPES.items():
        if pd.Series([t]).str.contains(rx, regex=True).iloc[0]:
            if k == "hf_api_with_auth" and "Authorization" not in t and "Bearer" not in t:
                continue
            return k
    return "other"


pos["type"] = pos["raw"].map(classify)  # raw: detok has already removed the URLs

# struct exact
bset = set(hashlib.sha1(s.encode()).hexdigest() for s in ben["struct"])
pos["struct_exact"] = [hashlib.sha1(s.encode()).hexdigest() in bset for s in pos["struct"]]

# struct near (MinHash over benign, query each swarm action)
lsh = MinHashLSH(threshold=0.8, num_perm=PERMS)
for i, s in enumerate(ben["struct"]):
    m = MinHash(num_perm=PERMS)
    for sh in _shingles(s):
        m.update(sh.encode())
    lsh.insert(str(i), m)
near, near_idx = [], []
for s in pos["struct"]:
    m = MinHash(num_perm=PERMS)
    for sh in _shingles(s):
        m.update(sh.encode())
    hits = lsh.query(m)
    near.append(bool(hits)); near_idx.append(int(hits[0]) if hits else -1)
pos["struct_near"], pos["struct_near_idx"] = near, near_idx
print("struct done", flush=True)

# detok cosine
vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=300_000, sublinear_tf=True, lowercase=False)
vec.fit(pd.concat([pos["detok"].str.slice(0, 2000), ben["detok"].str.slice(0, 2000)]))
B = vec.transform(ben["detok"].str.slice(0, 2000))
best, best_idx = np.zeros(len(pos)), np.zeros(len(pos), dtype=int)
for i in range(0, len(pos), 2000):
    P = vec.transform(pos["detok"].iloc[i : i + 2000].str.slice(0, 2000))
    S = (P @ B.T).toarray()
    best[i : i + 2000], best_idx[i : i + 2000] = S.max(1), S.argmax(1)
pos["detok_maxcos"], pos["detok_best_idx"] = best, best_idx
print("detok done", flush=True)


def rates(d):
    cw = d.groupby("cluster")[["struct_exact", "struct_near"]].mean().mean()
    cwc = d.assign(c9=d.detok_maxcos >= 0.9, c8=d.detok_maxcos >= 0.8).groupby("cluster")[["c9", "c8"]].mean().mean()
    return {
        "n_rows": len(d), "n_clusters": int(d.cluster.nunique()),
        "struct_exact_rows": round(float(d.struct_exact.mean()), 4), "struct_exact_clusterw": round(float(cw.struct_exact), 4),
        "struct_near_rows": round(float(d.struct_near.mean()), 4), "struct_near_clusterw": round(float(cw.struct_near), 4),
        "detok_cos>=0.9_rows": round(float((d.detok_maxcos >= 0.9).mean()), 4), "detok_cos>=0.9_clusterw": round(float(cwc.c9), 4),
        "detok_cos>=0.8_rows": round(float((d.detok_maxcos >= 0.8).mean()), 4), "detok_cos>=0.8_clusterw": round(float(cwc.c8), 4),
        "detok_maxcos_median": round(float(d.detok_maxcos.median()), 4),
    }


res = {"benign_pool": {"n": len(ben), "by_source": ben.source.value_counts().to_dict()}, "all": rates(pos), "by_type": {t: rates(g) for t, g in pos.groupby("type")}}
# twin examples for the write-up: highest detok cosine per type, and struct-exact pairs
ex = []
for t, g in pos.groupby("type"):
    for _, r in g.sort_values("detok_maxcos", ascending=False).drop_duplicates("cluster").head(3).iterrows():
        b = ben.iloc[int(r.detok_best_idx)]
        ex.append({"type": t, "swarm_id": r.id, "swarm_cite": r.cite, "swarm_detok": r.detok[:400], "benign_id": str(b.id), "benign_source": b.source, "benign_raw": b.raw[:400], "detok_cos": round(float(r.detok_maxcos), 4), "struct_exact": bool(r.struct_exact)})
res["twin_examples"] = ex
res["benign_twin_sources_struct_near"] = ben.iloc[[i for i in pos.struct_near_idx if i >= 0]].source.value_counts().to_dict()
json.dump(res, open(out, "w"), indent=1, default=str)
pos[["id", "cluster", "type", "struct_exact", "struct_near", "detok_maxcos"]].to_parquet(out.replace(".json", "_rows.parquet"), index=False)
print(json.dumps({k: res[k] for k in ("all", "by_type", "benign_twin_sources_struct_near")}, indent=1))
