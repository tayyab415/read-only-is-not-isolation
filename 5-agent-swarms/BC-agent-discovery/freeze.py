#!/usr/bin/env python3
"""Freeze BC: hash every code, prompt, schedule and protocol file and record the limits.

Refuses to overwrite FREEZE.json. `--check` recomputes the hashes and fails on drift
(the fleet runner calls it before launching each block).
Usage: freeze.py --decision-note "..."   |   freeze.py --check
"""
import argparse, datetime, hashlib, importlib.util, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import sys
if str(ROOT / "tasks") not in sys.path:
    sys.path.insert(0, str(ROOT / "tasks"))  # tasks/prompts.py imports make_fixtures
FREEZE = ROOT / "FREEZE.json"
CODE_DIRS = ["node", "harness", "tasks", "audit", "validation"]
SKIP_PARTS = {"__pycache__", "tests", "data-smoke", "blind"}
SKIP_NAMES = {"nodes.json", "ssh_config", "known_hosts", "verification.json", "scores.jsonl",
              "analysis.json", "tables.md", "reviews.json"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes():
    out = {}
    for d in CODE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and not (SKIP_PARTS & set(p.relative_to(ROOT).parts)) and p.name not in SKIP_NAMES \
                    and p.suffix in {".py", ".sh", ".md", ".txt", ".json", ".Dockerfile", ""} \
                    and not p.name.startswith(("controls-", "smoke-", "teardown-", "feasibility", "fleet-readback")):
                out[str(p.relative_to(ROOT))] = sha(p)
    for f in ("SPEC.md", "PROTOCOL.md", "freeze.py", "run_collection.sh"):
        out[f] = sha(ROOT / f)
    return out


def prompt_hashes():
    spec = importlib.util.spec_from_file_location("bc_prompts", ROOT / "tasks" / "prompts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"version": getattr(mod, "PROMPT_VERSION", None), "hashes": mod.prompt_hashes()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--decision-note", default="")
    a = ap.parse_args()
    if a.check:
        frozen = json.loads(FREEZE.read_text())
        now = code_hashes()
        drift = sorted(k for k in set(frozen["files"]) | set(now) if frozen["files"].get(k) != now.get(k))
        print(json.dumps({"drift": drift}))
        sys.exit(1 if drift else 0)
    assert not FREEZE.exists(), "FREEZE.json exists; a freeze is never rewritten"
    sched = json.loads((ROOT / "tasks" / "schedule.json").read_text())
    rows = sched["rows"] if isinstance(sched, dict) else sched
    rec = {
        "frozen_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "decision_note": a.decision_note,
        "files": code_hashes(),
        "prompts": prompt_hashes(),
        "schedule_rows": len(rows),
        "schedule_sha256": sha(ROOT / "tasks" / "schedule.json"),
        "models": {"glm": "zai-org/GLM-5.3-Flash:deepinfra", "deepseek": "deepseek-ai/DeepSeek-V4-Flash:deepinfra"},
        "generation": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 2048, "thinking": "provider default (no reasoning content returned in readiness check)"},
        "protocol_version": "v2 (v1 pilot: FREEZE-v1.json, data-v1-pilot/)",
        "limits": {"turns_per_actor": 20, "wall_s": 360, "tool_timeout_max_s": 60},
        "service": {"vllm": "0.33.0", "model": "Qwen/Qwen2.5-1.5B-Instruct", "gateway_policy": "salted",
                    "instance_type": "g6.xlarge"},
        "budget": {"hf_total_usd": 10.0, "hf_main_cap_usd": 8.5, "hf_confirmation_usd": 1.5, "aws_working_usd": 25.0, "aws_hard_usd": 35.0},
    }
    FREEZE.write_text(json.dumps(rec, indent=2) + "\n")
    print(json.dumps({k: rec[k] for k in ("frozen_utc", "schedule_rows", "schedule_sha256")}, indent=2))


if __name__ == "__main__":
    main()
