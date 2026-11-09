"""Shared evaluation subset for comparing our monitors with published guard models.

Seed-0 test split (sampled per class) plus every held-out set (large ones sampled). Guard text is the
detok view with the placeholder written as [REDACTED], so redaction looks the same in both classes.

Usage: python 14_guard_subset.py SPLIT_DIR OUT.parquet [--n-test 1000] [--max-eval 500]
"""
import argparse
import glob
import os
import re

import pandas as pd

from canon import PLACEHOLDER

ap = argparse.ArgumentParser()
ap.add_argument("split_dir"); ap.add_argument("out")
ap.add_argument("--n-test", type=int, default=1000); ap.add_argument("--max-eval", type=int, default=500)
a = ap.parse_args()

te = pd.read_parquet(f"{a.split_dir}/splits/seed0/test.parquet")
parts = [
    te[te.label == 1].sample(min(a.n_test, (te.label == 1).sum()), random_state=0).assign(set="test_swarm"),
    te[te.label == 0].sample(min(a.n_test, (te.label == 0).sum()), random_state=0).assign(set="test_benign"),
]
for p in sorted(glob.glob(f"{a.split_dir}/evals/*.parquet")):
    d = pd.read_parquet(p)
    parts.append(d.sample(min(a.max_eval, len(d)), random_state=0).assign(set=os.path.basename(p)[:-8]))
df = pd.concat(parts, ignore_index=True)
df["id"] = df["id"].astype(str)
df["guard_text"] = df["detok"].map(lambda t: re.sub(rf"\b{PLACEHOLDER}\b", "[REDACTED]", t)[:6000])
df[["id", "set", "source", "lang", "label", "guard_text", "detok", "raw"]].to_parquet(a.out, index=False)
print(df.groupby(["set", "label"]).size().to_string())
