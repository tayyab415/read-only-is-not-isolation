# /// script
# requires-python = ">=3.11"
# dependencies = ["torch>=2.4", "transformers==5.22.2", "pandas", "pyarrow", "huggingface_hub>=0.30", "sentencepiece", "protobuf"]
# ///
"""Score the shared guard subset with Llama Prompt Guard 2 (86M and 22M) on CPU.

Off-label use: Prompt Guard 2 detects prompt injection and jailbreaks in model inputs, not unsafe tool calls.
It is included because it is the System-1 guard people deploy. Score = logit(malicious) - logit(benign).

Usage: hf jobs uv run --flavor a10g-small --secrets HF_TOKEN guard_promptguard.py DATA_REPO RESULTS_REPO
"""
import json
import os
import sys
import time

import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

import re
from huggingface_hub import HfApi, snapshot_download
DATA, RESULTS = sys.argv[1], sys.argv[2]
local = snapshot_download(DATA, repo_type="dataset", allow_patterns=["guard_eval/*", "extra_evals/*"], local_dir="/tmp/d")
out_dir = "/tmp/pg"
os.makedirs(out_dir, exist_ok=True)
parts = [pd.read_parquet(f"{local}/guard_eval/subset.parquet")]
for f in sorted(os.listdir(f"{local}/extra_evals")):
    e = pd.read_parquet(f"{local}/extra_evals/{f}")
    e["set"] = f[:-8]
    e["guard_text"] = e["detok"].map(lambda t: re.sub(r"\bZQ\b", "[REDACTED]", t)[:6000])
    parts.append(e[["id", "set", "source", "label", "guard_text"]])
df = pd.concat(parts, ignore_index=True)
dev = "cuda" if torch.cuda.is_available() else "cpu"
for name in ("meta-llama/Llama-Prompt-Guard-2-86M", "meta-llama/Llama-Prompt-Guard-2-22M"):
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name).to(dev).eval()
    labels = model.config.id2label
    mal = [i for i, l in labels.items() if "MALICIOUS" in l.upper() or l.upper() in ("LABEL_1", "INJECTION", "JAILBREAK")]
    mal = int(mal[0]) if mal else 1
    scores = []
    t0 = time.time()
    with torch.inference_mode():
        for i in range(0, len(df), 32):
            b = tok(df["guard_text"].iloc[i : i + 32].tolist(), truncation=True, max_length=512, padding=True, return_tensors="pt").to(dev)
            lg = model(**b).logits.float().cpu()
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

HfApi().upload_folder(folder_path=out_dir, path_in_repo="guards/promptguard2", repo_id=RESULTS, repo_type="dataset", commit_message="Prompt Guard 2 zero-shot scores")
print("done", flush=True)
