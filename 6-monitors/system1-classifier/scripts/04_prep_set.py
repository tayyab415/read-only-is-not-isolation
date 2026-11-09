"""Turn any {id, source, raw[, lang]} table into a set the gate can use: adds detok, struct, cluster, label.

Usage: python 04_prep_set.py IN.(jsonl|parquet) OUT.parquet LABEL [--max-chars 4000] [--min-struct 12]
"""
import argparse
import json

import pandas as pd

from canon import detok, guess_lang, struct
from cluster import clusters

ap = argparse.ArgumentParser()
ap.add_argument("inp")
ap.add_argument("out")
ap.add_argument("label", type=int)
ap.add_argument("--max-chars", type=int, default=4000)
ap.add_argument("--min-struct", type=int, default=12)
a = ap.parse_args()

df = pd.read_json(a.inp, lines=True) if a.inp.endswith(".jsonl") else pd.read_parquet(a.inp)
n_in = len(df)
df = df.dropna(subset=["raw"])
df["raw"] = df["raw"].astype(str).str.slice(0, a.max_chars)
df = df.drop_duplicates(subset=["raw"])
df["detok"] = df["raw"].map(detok)
if "lang" not in df or df["lang"].isna().any():
    df["lang"] = df["detok"].map(guess_lang)
df["struct"] = [struct(r, l) for r, l in zip(df["raw"], df["lang"])]
df = df[df["struct"].str.split().str.len() >= a.min_struct].copy()
src = df["source"].iloc[0] if len(df) else "empty"
df["cluster"] = clusters(df["struct"].tolist(), f"{src}:")
df["label"] = a.label
df[["id", "source", "lang", "raw", "detok", "struct", "cluster", "label"]].to_parquet(a.out, index=False)
print(json.dumps({"in": n_in, "out": len(df), "clusters": df["cluster"].nunique(), "by_lang": df["lang"].value_counts().to_dict(), "by_source": df["source"].value_counts().to_dict()}))
