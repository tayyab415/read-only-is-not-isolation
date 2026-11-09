# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "torch>=2.4",
#   "transformers==5.22.2",
#   "accelerate>=1.0",
#   "datasets>=3.0",
#   "pandas",
#   "pyarrow",
#   "scikit-learn",
#   "huggingface_hub>=0.30",
#   "sentencepiece",
#   "protobuf",
# ]
# ///
"""Fine-tune a System-1 encoder classifier (one forward pass, score out, no generation) on prepared splits.

Reads   DATA_REPO/splits/seed{S}/{train,val,test}.parquet and DATA_REPO/evals/*.parquet (columns: id, text views, label)
Writes  RESULTS_REPO/<run_name>/seed{S}/scores_{split}.parquet  (id, label, score) and train_log.json
        optionally pushes the seed-0 model to MODEL_REPO.

Run on HF Jobs, e.g.:
  hf jobs uv run --flavor a10g-small --secrets HF_TOKEN train_encoder.py \
      --data-repo tyb343/s1-monitor-data --results-repo tyb343/s1-monitor-results --view detok --seeds 0 1 2
"""
import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import torch
from huggingface_hub import HfApi, snapshot_download
from sklearn.metrics import roc_auc_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding, Trainer, TrainingArguments
from datasets import Dataset

ap = argparse.ArgumentParser()
ap.add_argument("--data-repo", required=True)
ap.add_argument("--results-repo", required=True)
ap.add_argument("--model-repo", default=None)
ap.add_argument("--model", default="answerdotai/ModernBERT-base")
ap.add_argument("--model-revision", default="8949b909ec900327062f0ebf497f51aef5e6f0c8")
ap.add_argument("--view", default="detok", choices=["raw", "detok", "struct"])
ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
ap.add_argument("--max-len", type=int, default=512)
ap.add_argument("--epochs", type=float, default=2)
ap.add_argument("--lr", type=float, default=5e-5)
ap.add_argument("--bs", type=int, default=32)
ap.add_argument("--run-name", default=None)
ap.add_argument("--smoke", action="store_true", help="tiny CPU run, no uploads")
a = ap.parse_args()
run_name = a.run_name or f"modernbert-{a.view}"

local = snapshot_download(a.data_repo, repo_type="dataset", local_dir="/tmp/data" if not a.smoke else "/tmp/s1smoke_data")
api = HfApi()
tok = AutoTokenizer.from_pretrained(a.model, revision=a.model_revision)
eval_files = sorted(f"{d}/{f}" for d in ("evals", "extra_evals") if os.path.isdir(f"{local}/{d}") for f in os.listdir(f"{local}/{d}") if f.endswith(".parquet"))
print("evals:", eval_files, "gpu:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none", flush=True)


def to_ds(df):
    ds = Dataset.from_pandas(pd.DataFrame({"text": df[a.view].astype(str), "label": df["label"].astype(int)}))
    return ds.map(lambda b: tok(b["text"], truncation=True, max_length=a.max_len), batched=True, remove_columns=["text"])


def score(trainer, df):
    out = trainer.predict(to_ds(df))
    logits = out.predictions[0] if isinstance(out.predictions, tuple) else out.predictions
    return (logits[:, 1] - logits[:, 0]).astype(np.float32)


for seed in a.seeds:
    d = f"{local}/splits/seed{seed}"
    tr, va, te = (pd.read_parquet(f"{d}/{s}.parquet") for s in ("train", "val", "test"))
    if a.smoke:
        tr, va, te = tr.sample(48, random_state=0), va.sample(32, random_state=0), te.sample(32, random_state=0)
    print(f"seed {seed}: train {len(tr)} val {len(va)} test {len(te)}", flush=True)
    torch.manual_seed(seed)
    model = AutoModelForSequenceClassification.from_pretrained(a.model, revision=a.model_revision, num_labels=2)
    args = TrainingArguments(
        output_dir=f"/tmp/out/seed{seed}",
        per_device_train_batch_size=a.bs,
        per_device_eval_batch_size=a.bs * 2,
        learning_rate=a.lr,
        num_train_epochs=a.epochs,
        max_steps=2 if a.smoke else -1,
        warmup_steps=0.06,  # transformers 5: a float below 1 is a ratio
        weight_decay=0.01,
        lr_scheduler_type="linear",
        bf16=torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
        logging_steps=50,
        save_strategy="no",
        eval_strategy="no",
        report_to=[],
        seed=seed,
        dataloader_num_workers=2,
    )
    trainer = Trainer(model=model, args=args, train_dataset=to_ds(tr), data_collator=DataCollatorWithPadding(tok), processing_class=tok)
    t0 = time.time()
    trainer.train()
    train_s = time.time() - t0

    outdir = f"/tmp/results/{run_name}/seed{seed}"
    os.makedirs(outdir, exist_ok=True)
    log = {"seed": seed, "view": a.view, "model": a.model, "model_revision": a.model_revision, "train_seconds": train_s, "n_train": len(tr), "args": vars(a), "loss_history": [h for h in trainer.state.log_history if "loss" in h]}
    sets = {"val": va, "test": te} | {f"eval_{os.path.basename(f)[:-8]}": pd.read_parquet(f"{local}/{f}") for f in eval_files}
    if a.smoke:
        sets = {k: v.head(16) for k, v in sets.items()}
    for name, df in sets.items():
        t0 = time.time()
        s = score(trainer, df)
        log[f"score_seconds_{name}"] = time.time() - t0
        pd.DataFrame({"id": df["id"].astype(str), "source": df["source"], "label": df["label"].astype(int), "score": s}).to_parquet(f"{outdir}/scores_{name}.parquet", index=False)
        if df["label"].nunique() == 2:
            log[f"auc_{name}"] = float(roc_auc_score(df["label"], s))
        print(f"  seed {seed} {name}: n={len(df)} {log.get(f'auc_{name}', '')}", flush=True)
    json.dump(log, open(f"{outdir}/train_log.json", "w"), indent=1)
    if a.smoke:
        print(json.dumps({k: v for k, v in log.items() if k != "loss_history" and k != "args"}, default=str)[:600]); break
    api.upload_folder(folder_path=outdir, path_in_repo=f"{run_name}/seed{seed}", repo_id=a.results_repo, repo_type="dataset", commit_message=f"{run_name} seed {seed}")
    if a.model_repo and seed == a.seeds[0]:
        trainer.save_model(f"/tmp/model/{run_name}")
        tok.save_pretrained(f"/tmp/model/{run_name}")
        api.upload_folder(folder_path=f"/tmp/model/{run_name}", path_in_repo=run_name, repo_id=a.model_repo, repo_type="model", commit_message=f"{run_name} seed {seed}")
    del model, trainer
    torch.cuda.empty_cache()
print("done", flush=True)
