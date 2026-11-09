#!/usr/bin/env python3
"""S4: does running each tenant on its own vLLM process on the same GPU stop the load channel?

Two vLLM 0.33.0 processes of the same model share one L4 (ports 8000 and 8001, each
--gpu-memory-utilization 0.42, prefix caching on). Tenant A reaches only instance A through a
salted gateway on 9101; tenant B reaches only instance B through a salted gateway on 9102.
The S3 symbol stream is reused unchanged (S3's run_block): tenant A sends one ordinary request
per 1-bit, tenant B probes 10 ms after each slot start. Arms: T100_sep, T200_sep.
Usage: trials_s4.py run --split calibration|holdout | freeze
"""
import argparse, hashlib, json, random, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import trials as T  # noqa: E402
import trials_s3 as S3  # noqa: E402

PHASE = "s4"
S3.PHASE = PHASE
ARMS = {"T100_sep": (0.100, False), "T200_sep": (0.200, False)}
S3.ARMS.update(ARMS)
N = {"calibration": 200, "holdout": 600}
BLOCK = 50
PORT_A, PORT_B = 9101, 9102


def schedule(split):
    rng = random.Random(f"S4-{split}")
    blocks = []
    for arm in ARMS:
        bits = [1] * (N[split] // 2) + [0] * (N[split] // 2)
        rng.shuffle(bits)
        for b0 in range(0, len(bits), BLOCK):
            blocks.append((arm, b0, bits[b0:b0 + BLOCK]))
    rng.shuffle(blocks)
    return blocks


def run(split):
    out_path = T.DATA / f"trials-{PHASE}-{split}.jsonl"
    assert not out_path.exists(), f"{out_path} exists; a split is collected once"
    if split == "holdout":
        assert (T.DATA / f"thresholds-{PHASE}.json").exists(), "freeze thresholds before holdout"
    lim = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    ca = httpx.Client(base_url=f"http://127.0.0.1:{PORT_A}", timeout=120, limits=lim)
    cb = httpx.Client(base_url=f"http://127.0.0.1:{PORT_B}", timeout=120, limits=lim)
    for i in range(10):
        T.chat(ca, "tenant-a", T.words(f"warm4-a-{i}", 50), f"warm4-{i}", "warmup")
        T.chat(cb, "tenant-b", T.words(f"warm4-b-{i}", 50), f"warm4-{i}", "warmup")
    with out_path.open("a") as out:
        for arm, b0, bits in schedule(split):
            S3.run_block(split, arm, b0, bits, ca, cb, out)
            time.sleep(0.5)
    print("wrote", out_path)


def freeze():
    rows = [json.loads(l) for l in (T.DATA / f"trials-{PHASE}-calibration.jsonl").read_text().splitlines()]
    th = {}
    for arm in ARMS:
        rs = [r for r in rows if r["arm"] == arm and r["receiver"]["status"] == 200]
        t, acc = T.fit_threshold([r["receiver"]["ms"] for r in rs], [r["bit"] for r in rs], low_means_one=False)
        th[arm] = {"threshold_ms": t, "low_means_one": False, "calibration_accuracy": acc, "n": len(rs)}
    src = T.DATA / f"trials-{PHASE}-calibration.jsonl"
    payload = {"phase": PHASE, "thresholds": th, "frozen_utc_ns": time.time_ns(),
               "calibration_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
               "trials_s4_py_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (T.DATA / f"thresholds-{PHASE}.json").write_text(json.dumps(payload, indent=1))
    print(json.dumps(payload, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "freeze"])
    ap.add_argument("--split", choices=list(N))
    a = ap.parse_args()
    run(a.split) if a.cmd == "run" else freeze()
