"""Markdown tables from 07_eval.py outputs (mean ± std over seeds).

Usage: python 11_tables.py RESULT.json [RESULT2.json ...] > tables.md
"""
import json
import sys

import pandas as pd

PAIRS = {"agentapprove": ("A_agentapprove", "B_agentapprove"), "tomngdev": ("A_tomng_deny", "B_tomng_allow"), "rogue": ("A_rogue_unsafe", "B_rogue_safe")}
rows = []
for path in sys.argv[1:]:
    r = json.load(open(path))
    tag = path.split("/")[-1].replace(".json", "")
    for run in r["runs"]:
        rows.append({"result": tag, **run})
df = pd.DataFrame(rows)


def ms(series, pct=True):
    m, s = series.mean(), series.std()
    if pd.isna(m):
        return "–"
    return f"{100*m:.1f} ± {100*s:.1f}" if pct else f"{m:.3f} ± {s:.3f}"


def table(cols, title, pct=True):
    print(f"\n### {title}\n")
    header = ["result", "model"] + [c.split(":", 1)[-1] for c in cols]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for (res, model), g in df.groupby(["result", "model"], sort=False):
        cells = [ms(g[c], pct) if c in g else "–" for c in cols]
        print(f"| {res} | {model} | " + " | ".join(cells) + " |")


table(["test_auc", "test_tpr@thr1", "test_fpr@thr1", "test_tpr@thr01"], "In-distribution test (held-out Swarm Traces clusters vs held-out training-source benign). AUC and rates in %", pct=True)
table(sorted(c for c in df.columns if c.startswith("eval_fpr@thr1:")), "False-positive rate (%) on benign sources never seen in training, at the 1%-val-FPR threshold")
table(sorted(c for c in df.columns if c.startswith("eval_tpr@thr1:")), "Detection rate (%) on attacks from other sources, at the same threshold")
for name, (a, b) in PAIRS.items():
    df[f"paired:{name}"] = df.get(f"eval_auc:{a}_vs_{b}")
table([f"paired:{n}" for n in PAIRS], "Paired AUC within one outside dataset (its attacks vs its own benign rows). 0.5 = chance", pct=False)
