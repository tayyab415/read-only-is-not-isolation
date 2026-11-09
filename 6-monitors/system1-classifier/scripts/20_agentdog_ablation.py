"""Ablation subset for AgentDoG: does per-call flagging come from missing task context or from [REDACTED] placeholders?

Benign Luna/GLM 5 agent scripts (they carry their real task text) in three conditions, HF-API doc snippets in two:
  neutral_redacted  neutral user turn, detok text with [REDACTED]   (the main-run condition)
  neutral_raw       neutral user turn, raw text (URLs intact)
  task_raw          the script's real task as the user turn, raw text   (gen sets only)
Usage: python 20_agentdog_ablation.py OUT.parquet
"""
import json, re, sys
import pandas as pd
from canon import PLACEHOLDER

rows = []
tasks = {}
for fam in ("luna", "glm5"):
    for l in open(f"../data/src/src_gen_{fam}.jsonl"):
        r = json.loads(l); tasks[r["id"]] = r.get("task", "")
for name, path, n in (("gen_luna", "../data/split_v1/evals/B_gen_luna.parquet", 150), ("gen_glm5", "../data/split_v1/evals/B_gen_glm5.parquet", 150), ("hf_docs_api", "../data/split_v1/evals/B_hf_docs_api.parquet", 150)):
    d = pd.read_parquet(path).sample(n, random_state=7)
    for r in d.itertuples():
        red = re.sub(rf"\b{PLACEHOLDER}\b", "[REDACTED]", r.detok)[:6000]
        base = {"id": str(r.id), "source": r.source, "label": 0, "lang": r.lang}
        rows.append(base | {"set": f"{name}|neutral_redacted", "guard_text": red, "user_msg": None})
        rows.append(base | {"set": f"{name}|neutral_raw", "guard_text": r.raw[:6000], "user_msg": None})
        if name.startswith("gen_") and tasks.get(str(r.id)):
            rows.append(base | {"set": f"{name}|task_raw", "guard_text": r.raw[:6000], "user_msg": tasks[str(r.id)]})
df = pd.DataFrame(rows)
df.to_parquet(sys.argv[1], index=False)
print(df.set.value_counts().to_string())
