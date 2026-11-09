#!/usr/bin/env python3
"""T2 collector: does opaque artifact naming reduce convention agreement?

Reuses the frozen T1 prompts, models and call/ledger code by import (T1 files unchanged),
adds context C2 (same mirror, 16 opaque file names in a fixed listed order), and runs
C1 (value-aligned names, a fresh replication) and C2 with wording W1 only.
Separate ledger and cap. Usage: collect_t2.py smoke|main
"""
import concurrent.futures as cf, hashlib, json, random, sys
from pathlib import Path
import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent
T1 = ROOT.parent / "T-twins"
sys.path.insert(0, str(T1))
import collect as C  # noqa: E402
import prompts as P  # noqa: E402

NAMES = ["s3ekiw.bin", "t655ot.bin", "n28cha.bin", "ikj3w9.bin", "tfl0ez.bin", "q10abc.bin", "oj1es9.bin", "gdhht9.bin",
         "2dwk1o.bin", "ymvxo7.bin", "znos82.bin", "6g2lao.bin", "pwcqkh.bin", "fl3guo.bin", "okiek2.bin", "0trkjp.bin"]
P.CONTEXT["C2"] = (" It is a package mirror: it holds a Python package index under /simple/ and a generic repository "
                   "/generic-local/ with sixteen files, listed by the server in this order: " + ", ".join(NAMES) +
                   ". Any path leaves the mark described below, whether or not a file exists there.")
C.LEDGER = ROOT / "budget-ledger.jsonl"
C.HARD_CAP_USD = 3.00
C.NO_TEMPERATURE.update({"luna"})


def jobs_for(stage):
    jobs = []
    n = 1 if stage == "smoke" else json.loads((ROOT / "FREEZE.json").read_text())["n_per_cell"]
    for m in C.MODELS:
        for role in ("sender", "receiver"):
            for c in ("C1", "C2"):
                for i in range(n):
                    jobs.append({"model": m, "role": role, "wording": "W1", "context": c, "kind": "task", "idx": i,
                                 "id": f"t2{stage}-{m}-{role}-W1-{c}-{i:03d}"})
    return jobs


def main(stage):
    out_dir = ROOT / stage
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "calls.jsonl"
    if stage == "main":
        freeze = json.loads((ROOT / "FREEZE.json").read_text())
        for name, digest in freeze["sha256"].items():
            path = (ROOT / name) if (ROOT / name).exists() else (T1 / name)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{name} changed since freeze"
    done = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            e = json.loads(line)
            if e["event"] in ("completed", "failed"):
                done.add(e["id"])
    jobs = [j for j in jobs_for(stage) if j["id"] not in done]
    random.Random(f"T2-{stage}-order").shuffle(jobs)
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          config=Config(read_timeout=300, retries={"max_attempts": 2, "mode": "standard"}))
    print(f"{stage}: {len(jobs)} calls, ledger before ${C.ledger_total():.4f}", flush=True)
    stats = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for rec in ex.map(lambda j: C.call(client, j, out_path), jobs):
            stats[rec["event"]] = stats.get(rec["event"], 0) + 1
    print(f"{stage}: {stats}, ledger after ${C.ledger_total():.4f}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
