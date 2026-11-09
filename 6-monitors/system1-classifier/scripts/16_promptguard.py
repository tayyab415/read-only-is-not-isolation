"""Score the shared guard subset with Llama Prompt Guard 2 (86M and 22M) on CPU.

Off-label use: Prompt Guard 2 detects prompt injection and jailbreaks in model inputs, not unsafe tool calls.
It is included because it is the System-1 guard people deploy. Score = logit(malicious) - logit(benign).

Usage: python 16_promptguard.py SUBSET.parquet OUT_DIR
"""
import json
import os
import sys
import time

import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

subset, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
df = pd.read_parquet(subset)
torch.set_num_threads(3)
for name in ("meta-llama/Llama-Prompt-Guard-2-86M", "meta-llama/Llama-Prompt-Guard-2-22M"):
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name).eval()
    labels = model.config.id2label
    mal = [i for i, l in labels.items() if "MALICIOUS" in l.upper() or l.upper() in ("LABEL_1", "INJECTION", "JAILBREAK")]
    mal = int(mal[0]) if mal else 1
    scores = []
    t0 = time.time()
    with torch.inference_mode():
        for i in range(0, len(df), 32):
            b = tok(df["guard_text"].iloc[i : i + 32].tolist(), truncation=True, max_length=512, padding=True, return_tensors="pt")
            lg = model(**b).logits
            scores += (lg[:, mal] - lg[:, 1 - mal]).tolist() if lg.shape[1] == 2 else lg[:, mal].tolist()
    secs = time.time() - t0
    res = df[["id", "set", "source", "label"]].copy()
    res["score"] = scores
    short = name.split("/")[-1]
    res.to_parquet(f"{out_dir}/{short}.parquet", index=False)
    summ = {"model": name, "id2label": labels, "malicious_index": mal, "rows": len(res), "seconds": secs, "ms_per_row_batch32_3threads": 1000 * secs / len(res),
            "flag_rate_at_default_0": res.assign(f=res.score > 0).groupby("set")["f"].mean().round(4).to_dict()}
    json.dump(summ, open(f"{out_dir}/{short}.json", "w"), indent=1, default=str)
    print(json.dumps(summ, indent=1, default=str), flush=True)
