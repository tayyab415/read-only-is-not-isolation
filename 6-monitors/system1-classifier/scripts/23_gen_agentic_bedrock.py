"""Natural benign agent tool calls: a model acts as an agent and emits ONE tool call for a task. No style instructions.

This is the robustness check for the dialect control: 17_gen_benign_bedrock.py asked for the swarm's console style
explicitly; here the model writes code however it naturally would. Tasks come from other families' task lists.

Usage: python3 23_gen_agentic_bedrock.py MODEL_ID SOURCE OUT_DIR TASKS_JSONL [TASKS_JSONL ...] [--n 500] [--workers 8]
"""
import argparse
import concurrent.futures as cf
import json
import random
import time

import boto3

ap = argparse.ArgumentParser()
ap.add_argument("model_id"); ap.add_argument("source"); ap.add_argument("out_dir"); ap.add_argument("tasks", nargs="+")
ap.add_argument("--n", type=int, default=500); ap.add_argument("--workers", type=int, default=8)
a = ap.parse_args()
client = boto3.client("bedrock-runtime", region_name="us-east-1")

tasks = []
for path in a.tasks:
    for l in open(path):
        t = json.loads(l).get("task", "").strip()
        if len(t) > 15:
            tasks.append(t)
random.Random(0).shuffle(tasks)
tasks = list(dict.fromkeys(tasks))[: a.n]

SYSTEM = ("You are an autonomous agent working for your user. You can run code in the user's open web browser tab and in a Linux "
          "container. Complete the user's task by calling exactly one tool. Any tokens or credentials the user mentions are available "
          "as variables or environment variables named after them.")
TOOLS = {"tools": [
    {"toolSpec": {"name": "execute_javascript", "description": "Run JavaScript in the user's current browser tab (devtools console). Returns the value of the last expression.",
                  "inputSchema": {"json": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}}},
    {"toolSpec": {"name": "execute_python", "description": "Run a Python 3 script in the Linux container. Returns stdout.",
                  "inputSchema": {"json": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}}},
    {"toolSpec": {"name": "execute_bash", "description": "Run a bash command in the Linux container. Returns stdout and stderr.",
                  "inputSchema": {"json": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}},
]}
LANG = {"execute_javascript": "js", "execute_python": "py", "execute_bash": "sh"}


def call(i):
    msg = [{"role": "user", "content": [{"text": tasks[i]}]}]
    for attempt in range(4):
        try:
            r = client.converse(modelId=a.model_id, system=[{"text": SYSTEM}], messages=msg, toolConfig=TOOLS, inferenceConfig={"maxTokens": 4000})
            uses = [c["toolUse"] for c in r["output"]["message"]["content"] if "toolUse" in c]
            return {"i": i, "task": tasks[i], "tool_uses": uses, "stop": r.get("stopReason"), "usage": r["usage"]}
        except Exception as e:
            err = str(e)
            time.sleep(2 ** attempt)
    return {"i": i, "task": tasks[i], "tool_uses": [], "error": err}


rows, usage, no_tool = [], {"inputTokens": 0, "outputTokens": 0}, 0
with cf.ThreadPoolExecutor(a.workers) as ex, open(f"{a.out_dir}/raw_{a.source}.jsonl", "w") as raw:
    for res in ex.map(call, range(len(tasks))):
        raw.write(json.dumps(res) + "\n")
        for k in usage:
            usage[k] += res.get("usage", {}).get(k, 0)
        if not res["tool_uses"]:
            no_tool += 1
            continue
        u = res["tool_uses"][0]
        code = (u.get("input") or {}).get("code") or (u.get("input") or {}).get("command") or ""
        if len(code.strip()) >= 20:
            rows.append({"id": f"{a.source}:{res['i']}", "source": a.source, "raw": code.strip(), "lang": LANG.get(u.get("name"), "js"), "task": res["task"], "tool": u.get("name")})
with open(f"{a.out_dir}/src_{a.source}.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
from collections import Counter
print(json.dumps({"model": a.model_id, "source": a.source, "tasks": len(tasks), "tool_calls": len(rows), "no_tool": no_tool, "by_tool": Counter(r["tool"] for r in rows), "usage": usage}))
