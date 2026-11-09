#!/usr/bin/env python3
"""T1 collector: independent convention tables from Bedrock subject models.

Every call is independent (fresh conversation, no other agent's output). Calls are
logged in full to <stage>/calls.jsonl, and spend goes through a reserve/commit ledger
with a hard cap. Usage: collect.py smoke|ready|main
"""
import concurrent.futures as cf, hashlib, json, random, sys, threading, time, uuid
from pathlib import Path
import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import prompts as P  # noqa: E402
LEDGER = ROOT / "budget-ledger.jsonl"
HARD_CAP_USD = 6.00
GUARD = 1.20
MAX_TOKENS = 4000
# model key -> (Bedrock id, developer, USD per 1M input, USD per 1M output); prices from
# pricing/bedrock-us-east-1-ondemand.json (AWS Pricing API, 2026-11-09), Luna from the N ledger.
MODELS = {
    "luna":     ("us.openai.gpt-6-luna", "OpenAI", 0.11, 0.55),
    "oss120b":  ("openai.gpt-oss-120b-1:0", "OpenAI", 0.15, 0.60),
    "deepseek": ("deepseek.v3.2", "DeepSeek", 0.62, 1.85),
    "mistral":  ("mistral.mistral-large-3-675b-instruct", "Mistral", 0.50, 1.50),
    "llama4":   ("us.meta.llama4-maverick-17b-instruct-v1:0", "Meta", 0.24, 0.97),
    "glm5":     ("zai.glm-5", "Zhipu", 1.00, 3.20),
    "qwen3":    ("qwen.qwen3-next-80b-a3b", "Alibaba", 0.14, 1.20),
}
TEMPERATURE = 1.0
NO_TEMPERATURE = {"luna"}  # Bedrock rejected temperature for Luna in smoke (ValidationException)
LOCK = threading.Lock()
RUN_ID = str(uuid.uuid4())


def ledger_total():
    if not LEDGER.exists():
        return 0.0
    res, com = {}, {}
    for line in LEDGER.read_text().splitlines():
        e = json.loads(line)
        (res if e["event"] == "reserved" else com)[e["call_key"]] = e["usd"]
    return sum(com.get(k, v) for k, v in res.items())


def ledger_append(entry):
    with LEDGER.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def cost(model, usage):
    _, _, pin, pout = MODELS[model]
    return (usage.get("inputTokens", 0) * pin + usage.get("outputTokens", 0) * pout) / 1e6


def build(role, wording, kind, context=None):
    if kind == "ready":
        return P.READY_SENDER if role == "sender" else P.READY_RECEIVER
    base = (P.SENDER if role == "sender" else P.RECEIVER)[wording]
    return base.replace("{context}", P.CONTEXT[context])


def call(client, job, out_path):
    model, role, wording, kind = job["model"], job["role"], job["wording"], job["kind"]
    text = build(role, wording, kind, job.get("context"))
    req = {"modelId": MODELS[model][0], "system": [{"text": P.SYSTEM}],
           "messages": [{"role": "user", "content": [{"text": text}]}],
           "inferenceConfig": {"maxTokens": MAX_TOKENS}}
    if model not in NO_TEMPERATURE:
        req["inferenceConfig"]["temperature"] = TEMPERATURE
    key = f"{RUN_ID}/{job['id']}"
    reserve = GUARD * (len(P.SYSTEM + text) / 3 * MODELS[model][2] + MAX_TOKENS * MODELS[model][3]) / 1e6
    with LOCK:
        if ledger_total() + reserve > HARD_CAP_USD:
            rec = {"event": "budget_stop", **job, "ledger_usd": ledger_total()}
            with out_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            return rec
        ledger_append({"event": "reserved", "call_key": key, "usd": reserve, "model": model, "utc_ns": time.time_ns()})
    t0 = time.time_ns()
    try:
        r = client.converse(**req)
        content = r["output"]["message"]["content"]
        visible = "".join(c.get("text", "") for c in content)
        usage = r.get("usage", {})
        actual = cost(model, usage)
        rec = {"event": "completed", **job, "call_key": key, "start_utc_ns": t0, "end_utc_ns": time.time_ns(),
               "request": req, "text": visible, "stop_reason": r.get("stopReason"), "usage": usage,
               "usd": actual, "raw_content": content}
    except Exception as e:
        actual = reserve  # unknown usage: keep the reservation
        rec = {"event": "failed", **job, "call_key": key, "start_utc_ns": t0, "end_utc_ns": time.time_ns(),
               "request": req, "error_type": type(e).__name__, "error": str(e)[:500]}
    with LOCK:
        ledger_append({"event": "committed", "call_key": key, "usd": actual, "model": model, "utc_ns": time.time_ns()})
        with out_path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    return rec


def jobs_for(stage):
    jobs = []
    if stage in ("smoke", "smoke2"):
        for m in MODELS:
            for role in ("sender", "receiver"):
                for w in ("W1", "W2"):
                    for c in (("C0",) if stage == "smoke" else ("C0", "C1")):
                        jobs.append({"model": m, "role": role, "wording": w, "context": c, "kind": "task", "idx": 0})
    elif stage == "ready":
        for m in MODELS:
            for role in ("sender", "receiver"):
                for i in range(3):
                    jobs.append({"model": m, "role": role, "wording": "R", "context": None, "kind": "ready", "idx": i})
    elif stage == "main":
        n = json.loads((ROOT / "FREEZE.json").read_text())["n_per_cell"]
        for m in MODELS:
            for role in ("sender", "receiver"):
                for w in ("W1", "W2"):
                    for c in ("C0", "C1"):
                        for i in range(n):
                            jobs.append({"model": m, "role": role, "wording": w, "context": c, "kind": "task", "idx": i})
    for j in jobs:
        j["id"] = f"{stage}-{j['model']}-{j['role']}-{j['wording']}-{j['context']}-{j['idx']:03d}"
    return jobs


def main(stage):
    out_dir = ROOT / stage
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "calls.jsonl"
    if stage == "main":
        freeze = json.loads((ROOT / "FREEZE.json").read_text())
        for name, digest in freeze["sha256"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, f"{name} changed since freeze"
        NO_TEMPERATURE.update(freeze.get("no_temperature", []))
    done = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            e = json.loads(line)
            if e["event"] == "completed" or (stage == "main" and e["event"] == "failed"):
                done.add(e["id"])
    jobs = [j for j in jobs_for(stage) if j["id"] not in done]
    random.Random(f"T1-{stage}-order").shuffle(jobs)  # interleave conditions over time
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          config=Config(read_timeout=300, retries={"max_attempts": 2, "mode": "standard"}))
    print(f"{stage}: {len(jobs)} calls, ledger before ${ledger_total():.4f}", flush=True)
    stats = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for rec in ex.map(lambda j: call(client, j, out_path), jobs):
            stats[rec["event"]] = stats.get(rec["event"], 0) + 1
    print(f"{stage}: {stats}, ledger after ${ledger_total():.4f}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
