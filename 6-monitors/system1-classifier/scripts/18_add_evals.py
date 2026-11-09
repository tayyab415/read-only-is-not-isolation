"""Add extra held-out eval sets to an existing split dir with the same caps as 06_make_splits.py (10 per cluster, max 3000).

Usage: python 18_add_evals.py SPLIT_DIR NAME=PREPPED.parquet:LABEL [...]
"""
import sys

import numpy as np
import pandas as pd

split_dir = sys.argv[1]
rng = np.random.RandomState(1234)
COLS = ["id", "source", "lang", "raw", "detok", "struct", "cluster", "label"]
for spec in sys.argv[2:]:
    name, rest = spec.split("=", 1)
    path, label = rest.rsplit(":", 1)
    df = pd.read_parquet(path)[COLS]
    df["label"] = int(label)
    df = df.sample(frac=1, random_state=rng).groupby("cluster").head(10)
    df = df.sample(min(len(df), 3000), random_state=rng).reset_index(drop=True)
    df.to_parquet(f"{split_dir}/evals/{name}.parquet", index=False)
    print(name, len(df), "rows,", df.cluster.nunique(), "clusters", df.lang.value_counts().to_dict())
