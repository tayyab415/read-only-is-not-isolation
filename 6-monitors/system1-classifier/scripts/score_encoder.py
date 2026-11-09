# /// script
# requires-python = ">=3.11"
# dependencies = ["torch>=2.4", "transformers==5.22.2", "pandas", "pyarrow", "huggingface_hub>=0.30"]
# ///
"""Score extra eval parquets with saved seed-0 encoders (tyb343/s1-monitor-encoder/<run>), plus seed-0 val for the threshold.

  hf jobs uv run --flavor a10g-small --secrets HF_TOKEN score_encoder.py --data-repo tyb343/s1-monitor-data-v1 \
     --model-repo tyb343/s1-monitor-encoder --results-repo tyb343/s1-monitor-results --runs v1-modernbert-detok v1-modernbert-struct
"""
import argparse
import os

import pandas as pd
import torch
from huggingface_hub import HfApi, snapshot_download
from transformers import AutoModelForSequenceClassification, AutoTokenizer

ap = argparse.ArgumentParser()
ap.add_argument("--data-repo", required=True); ap.add_argument("--model-repo", required=True); ap.add_argument("--results-repo", required=True)
ap.add_argument("--runs", nargs="+", required=True); ap.add_argument("--extra-dir", default="extra_evals"); ap.add_argument("--max-len", type=int, default=512)
a = ap.parse_args()
data = snapshot_download(a.data_repo, repo_type="dataset", allow_patterns=[f"{a.extra_dir}/*", "splits/seed0/val.parquet"], local_dir="/tmp/d")
models = snapshot_download(a.model_repo, allow_patterns=[f"{r}/*" for r in a.runs], local_dir="/tmp/m")
api = HfApi()
dev = "cuda" if torch.cuda.is_available() else "cpu"
for run in a.runs:
    view = run.rsplit("-", 1)[1]
    tok = AutoTokenizer.from_pretrained(f"/tmp/m/{run}")
    model = AutoModelForSequenceClassification.from_pretrained(f"/tmp/m/{run}", torch_dtype=torch.bfloat16 if dev == "cuda" else torch.float32).to(dev).eval()
    out = f"/tmp/out/{run}"
    os.makedirs(out, exist_ok=True)
    files = {"val": "/tmp/d/splits/seed0/val.parquet"} | {f[:-8]: f"/tmp/d/{a.extra_dir}/{f}" for f in os.listdir(f"/tmp/d/{a.extra_dir}") if f.endswith(".parquet")}
    for name, path in files.items():
        df = pd.read_parquet(path)
        scores = []
        with torch.inference_mode():
            for i in range(0, len(df), 64):
                b = tok(df[view].astype(str).iloc[i : i + 64].tolist(), truncation=True, max_length=a.max_len, padding=True, return_tensors="pt").to(dev)
                lg = model(**b).logits.float()
                scores += (lg[:, 1] - lg[:, 0]).tolist()
        pd.DataFrame({"id": df["id"].astype(str), "source": df["source"], "label": df["label"].astype(int), "score": scores}).to_parquet(f"{out}/scores_{name}.parquet", index=False)
        print(run, name, len(df), flush=True)
    api.upload_folder(folder_path=out, path_in_repo=f"extra/{run}/seed0", repo_id=a.results_repo, repo_type="dataset", commit_message=f"extra eval scores {run}")
print("done", flush=True)
