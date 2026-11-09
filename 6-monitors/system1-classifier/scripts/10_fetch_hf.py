"""Extract agent-action text from the survey's picks into {id, source, raw, meta} JSONL files.

Small datasets were downloaded at pinned commits to ext/hf (see data_cards/survey.md for shas).
Large ones are filtered remotely with DuckDB over the Hub's parquet conversion (refs/convert/parquet).

Usage: python 10_fetch_hf.py OUT_DIR
"""
import json
import random
import re
import sys

import duckdb

OUT = sys.argv[1]
EXT = "../ext/hf"
random.seed(0)
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
PQ = "https://huggingface.co/datasets/{}/resolve/refs%2Fconvert%2Fparquet/{}"
stats = {}


def write(name, rows):
    with open(f"{OUT}/src_{name}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    stats[name] = len(rows)
    print(name, len(rows), flush=True)


CD_PREFIX = re.compile(r"^\s*cd\s+/workspace/\S+\s*(?:&&|;)\s*")


def bash_from_trajectories(url, col, source, n_traj):
    q = f"""
    WITH t AS (SELECT {col} AS msgs FROM read_parquet('{url}') LIMIT {n_traj}),
    c AS (SELECT unnest(msgs) AS m FROM t),
    tc AS (SELECT unnest(m.tool_calls) AS x FROM c WHERE m.role = 'assistant' AND m.tool_calls IS NOT NULL)
    SELECT x.function.arguments AS args FROM tc WHERE x.function.name = 'execute_bash'
    """
    rows = []
    for i, (args,) in enumerate(con.execute(q).fetchall()):
        try:
            cmd = json.loads(args).get("command", "")
        except Exception:
            continue
        cmd = CD_PREFIX.sub("", cmd).strip()
        if cmd:
            rows.append({"id": f"{source}:{i}", "source": source, "raw": cmd, "lang": "sh" if not cmd.startswith(("python", "cat >")) else "sh"})
    return rows


# --- A3 benign agent shell (training): nebius SWE-rebench OpenHands, sha 35455389
write("nebius_bash", bash_from_trajectories(PQ.format("nebius/SWE-rebench-openhands-trajectories", "default/train/0000.parquet"), "trajectory", "nebius_openhands_bash", 300))

# --- A3 benign agent shell (held out): SWE-Gym OpenHands sampled, sha baf3a4e4, shard 0000
write("swegym_bash", bash_from_trajectories(PQ.format("SWE-Gym/OpenHands-Sampled-Trajectories", "default/train.raw/0000.parquet"), "messages", "swegym_openhands_bash", 2019))

# --- A1 benign JS functions (training): CodeSearchNet javascript, sha bd0cf261
urls = [PQ.format("code-search-net/code_search_net", f"javascript/{s}/0000.parquet") for s in ("train", "validation", "test")]
pat = r"fetch\(|XMLHttpRequest|\$\.ajax|\$\.(get|post)\(|axios|document\.(body|getElementById|querySelector|createElement|write)|atob\(|btoa\(|eval\(|new Function|localStorage|new Image|\.innerHTML"
q = f"SELECT func_code_string, func_code_url FROM read_parquet({urls}) WHERE regexp_matches(func_code_string, '{pat}')"
write("csn_js", [{"id": u, "source": "codesearchnet_js", "raw": c, "lang": "js"} for c, u in con.execute(q).fetchall()])

# --- A2 benign JSON tool calls (held out): glaive v2, sha e7f4b645
q = f"SELECT chat FROM read_parquet('{PQ.format('glaiveai/glaive-function-calling-v2', 'default/train/0000.parquet')}') WHERE contains(chat, '<functioncall>') LIMIT 8000"
rows = []
for i, (chat,) in enumerate(con.execute(q).fetchall()):
    m = re.search(r"<functioncall>\s*(\{.*?\})\s*<\|endoftext\|>", chat, re.S)
    if m:
        rows.append({"id": f"glaive:{i}", "source": "glaive_calls", "raw": m.group(1), "lang": "js"})
write("glaive_calls", rows)

# --- A2 benign JSON tool calls (training): xlam 60k mirror, sha 26d14ebf
x = json.load(open(f"{EXT}/lockon_xlam-function-calling-60k/xlam_function_calling_60k.json"))
rows = []
for r in random.sample(x, 6000):
    for j, call in enumerate(json.loads(r["answers"])):
        rows.append({"id": f"xlam:{r['id']}:{j}", "source": "xlam_calls", "raw": json.dumps(call), "lang": "js"})
write("xlam_calls", rows)


# --- C / held-out benign: agentapprove coding-agent tool-call safety, sha 939f5b96 (main dev + holdout)
def aa_text(r):
    args = json.loads(r["tool_arguments"]) if isinstance(r["tool_arguments"], str) else r["tool_arguments"]
    if r["tool_name"] == "Bash":
        return args.get("command", ""), "sh"
    if r["tool_name"] == "Write":
        return args.get("content", ""), None
    if r["tool_name"] in ("Edit", "MultiEdit"):
        edits = args.get("edits") or [args]
        return "\n".join(e.get("new_string", "") for e in edits), None
    return json.dumps({"tool": r["tool_name"], "arguments": args}), "js"


ATTACK_RULES = ("read-credential-material", "share-credential-material", "download-and-execute-code")
att, ben = [], []
for split in ("dev", "holdout"):
    for line in open(f"{EXT}/agentapprove_coding-agent-tool-call-safety/data/main/{split}.jsonl"):
        r = json.loads(line)
        text, lang = aa_text(r)
        if not text.strip():
            continue
        truth = r["truth_tool_only"]
        row = {"id": r["id"], "source": "agentapprove", "raw": text, "meta": {"tool": r["tool_name"], "family": r["family"], "tags": r["tags"]}}
        if lang:
            row["lang"] = lang
        if any(truth.get(k) == "true" for k in ATTACK_RULES):
            att.append(row)
        elif all(v != "true" for v in truth.values()):
            ben.append(row)
write("agentapprove_attack", att)
write("agentapprove_benign", ben)

# --- C / held-out benign: tomngdev shell-safety v1.1 test split, sha fee89770 (deny vs allow; ask dropped)
deny, allow = [], []
for i, line in enumerate(open(f"{EXT}/tomngdev_shell-safety-v1.1/test.jsonl")):
    r = json.loads(line)
    row = {"id": f"tomng:{i}", "source": "tomngdev_shell", "raw": r["command"], "lang": "sh", "meta": {"category": r["category"], "reason": r.get("reason"), "shell": r["shell"]}}
    (deny if r["label"] == "deny" else allow if r["label"] == "allow" else []).append(row)
write("tomng_deny", deny)
write("tomng_allow", allow)

# --- C: rogue-security coding-agent benchmark (cc-by-nc-4.0, eval only), sha bf7ff748, tool calls only
import pandas as pd

d = pd.read_parquet(f"{EXT}/rogue-security_coding-agent-security-benchmark/data/test-00000-of-00001.parquet")
d = d[d.message_type == "tool call"]
write("rogue_unsafe", [{"id": f"rogue:{i}", "source": "rogue_security", "raw": r.data_to_evaluate, "lang": "js", "meta": {"label": r.label, "cat": r.category_and_criticality}} for i, r in d[d.label != "0"].iterrows()])
write("rogue_safe", [{"id": f"rogue:{i}", "source": "rogue_security", "raw": r.data_to_evaluate, "lang": "js"} for i, r in d[d.label == "0"].iterrows()])

# --- C: reverse shell payloads, sha 3593b32f (malicious only)
rows = []
for line in open(f"{EXT}/darkknight25_Reverse_Shell_Payloads_Dataset/Reverseshell_payloads_dataset.jsonl"):
    try:
        r = json.loads(line)
    except Exception:
        continue
    rows.append({"id": r["id"], "source": "revshell", "raw": r["payload"], "meta": {"language": r["language"]}})
write("revshell", rows)

json.dump(stats, open(f"{OUT}/fetch_stats.json", "w"), indent=1)
