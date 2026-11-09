"""Per-call cost of System-1 monitors on CPU: TF-IDF+LR vs a fine-tuned encoder.

Measures single-call latency (batch 1, includes detok preprocessing) and batched throughput on this machine.
Usage: python 09_latency.py SPLIT_DIR MODEL_DIR_OR_REPO OUT.json [--n 300] [--threads 4]
"""
import argparse
import json
import platform
import time

import numpy as np
import pandas as pd
import torch
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from canon import detok

ap = argparse.ArgumentParser()
ap.add_argument("split_dir"); ap.add_argument("model"); ap.add_argument("out")
ap.add_argument("--n", type=int, default=300); ap.add_argument("--threads", type=int, default=4)
ap.add_argument("--max-len", type=int, default=512)
a = ap.parse_args()
torch.set_num_threads(a.threads)

tr = pd.read_parquet(f"{a.split_dir}/splits/seed0/train.parquet")
te = pd.read_parquet(f"{a.split_dir}/splits/seed0/test.parquet").sample(a.n, random_state=0)
raw = te["raw"].tolist()
res = {"machine": platform.processor() or platform.machine(), "threads": a.threads, "n": a.n, "mean_raw_chars": float(np.mean([len(r) for r in raw]))}


def bench(fn, items):
    ts = []
    for x in items:
        t0 = time.perf_counter(); fn(x); ts.append((time.perf_counter() - t0) * 1000)
    return {"p50_ms": float(np.percentile(ts, 50)), "p95_ms": float(np.percentile(ts, 95)), "mean_ms": float(np.mean(ts))}


# TF-IDF + LR on detok
pipe = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=False), LogisticRegression(C=2.0, max_iter=3000, solver="liblinear"))
pipe.fit(tr["detok"].str.slice(0, 2000), tr["label"])
res["tfidf_detok_single"] = bench(lambda r: pipe.decision_function([detok(r)[:2000]]), raw)

# Encoder
tok = AutoTokenizer.from_pretrained(a.model)
model = AutoModelForSequenceClassification.from_pretrained(a.model).eval()
res["encoder_params"] = sum(p.numel() for p in model.parameters())


@torch.inference_mode()
def enc(texts):
    b = tok(texts, truncation=True, max_length=a.max_len, padding=True, return_tensors="pt")
    return model(**b).logits


for r in raw[:5]:
    enc([detok(r)])  # warm-up
res["encoder_single"] = bench(lambda r: enc([detok(r)]), raw)
t0 = time.perf_counter()
for i in range(0, len(raw), 32):
    enc([detok(r) for r in raw[i : i + 32]])
res["encoder_batch32_calls_per_s"] = len(raw) / (time.perf_counter() - t0)
json.dump(res, open(a.out, "w"), indent=1)
print(json.dumps(res, indent=1))
