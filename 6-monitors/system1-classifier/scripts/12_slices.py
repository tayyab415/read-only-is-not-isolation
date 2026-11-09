"""Which held-out benign rows get flagged? FPR by slice at the 1%-val-FPR threshold, per seed, for one model.

Usage: python 12_slices.py SPLIT_DIR SCORES_DIR MODEL_NAME OUT.json
"""
import glob, json, re, sys
import numpy as np, pandas as pd

split_dir, scores_dir, model, out = sys.argv[1:5]
SLICES = {
    "auth": r"Authorization|Bearer|[Tt]oken|HF_TOKEN|api[_-]?key",
    "network": r"fetch\(|XMLHttpRequest|curl |wget |requests\.|urllib|urlopen|axios|http[s]?://",
    "neither": None,
}
res = {}
for sd in sorted(glob.glob(f"{scores_dir}/{model}/seed*")):
    seed = sd.rsplit("seed", 1)[1]
    va = pd.read_parquet(f"{sd}/scores_val.parquet")
    thr = float(np.quantile(va.loc[va.label == 0, "score"], 0.99))
    for p in sorted(glob.glob(f"{sd}/scores_eval_B_*.parquet")):
        name = p.split("scores_eval_")[1][:-8]
        s = pd.read_parquet(p)
        ev = pd.read_parquet(f"{split_dir}/evals/{name}.parquet")[["id", "raw", "lang"]]
        ev["id"] = ev["id"].astype(str)
        d = s.merge(ev, on="id")
        d["flag"] = d.score > thr
        auth = d.raw.str.contains(SLICES["auth"]); net = d.raw.str.contains(SLICES["network"])
        for sl, mask in {"all": d.raw.notna(), "auth": auth, "network": net, "auth_and_network": auth & net, "neither": ~auth & ~net}.items():
            g = d[mask]
            res.setdefault(name, {}).setdefault(sl, []).append((int(g.flag.sum()), len(g)))
        for lang, g in d.groupby("lang"):
            res[name].setdefault(f"lang={lang}", []).append((int(g.flag.sum()), len(g)))
summary = {n: {sl: f"{100*np.mean([k/m for k,m in v if m]):.1f}% (n={v[0][1]})" for sl, v in s.items()} for n, s in res.items()}
json.dump({"model": model, "fpr_by_slice_mean_over_seeds": summary, "raw": res}, open(out, "w"), indent=1)
for n, s in summary.items():
    print(n, s)
