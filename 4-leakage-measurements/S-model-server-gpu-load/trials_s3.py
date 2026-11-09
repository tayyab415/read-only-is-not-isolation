#!/usr/bin/env python3
"""S3: bandwidth of the load channel that remains under the gateway-enforced salt.

A continuous symbol stream at a fixed period T through the salted gateway (port 9002).
At each slot start, tenant A sends one ordinary request (400 words, about 975 tokens,
1 output token) if the bit is 1 and nothing if it is 0. 10 ms after the slot start,
tenant B sends a fresh 100-word probe (1 output token) and records its latency. Requests
run in threads so the slot clock keeps time; a slow probe can overlap the next slot.
Arms: T50, T100, T200, T400 on an otherwise idle server; T200_busy with tenant C running
4 threads of ordinary requests (S1's Busy). Reuses S1 helpers by import; S1 files unchanged.
Usage: trials_s3.py run --split calibration|holdout | freeze
"""
import argparse, hashlib, json, random, sys, threading, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import trials as T  # noqa: E402

PHASE = "s3"
ARMS = {"T50": (0.050, False), "T100": (0.100, False), "T200": (0.200, False), "T400": (0.400, False),
        "T200_busy": (0.200, True)}
N = {"calibration": 200, "holdout": 600}
BLOCK = 50
PORT = 9002
SENDER_WORDS, PROBE_WORDS, PROBE_LEAD_S = 400, 100, 0.010


def schedule(split):
    rng = random.Random(f"S3-{split}")
    blocks = []
    for arm in ARMS:
        bits = [1] * (N[split] // 2) + [0] * (N[split] // 2)
        rng.shuffle(bits)
        for b0 in range(0, len(bits), BLOCK):
            blocks.append((arm, b0, bits[b0:b0 + BLOCK]))
    rng.shuffle(blocks)
    return blocks


def sleep_until(t):
    while True:
        d = t - time.perf_counter()
        if d <= 0:
            return
        time.sleep(d - 0.002 if d > 0.003 else 0)


def run_block(split, arm, b0, bits, ca, cb, out):
    period, busy = ARMS[arm]
    ctx = T.Busy(PORT, f"{PHASE}-{split}-{arm}-{b0:04d}") if busy else None
    if ctx:
        ctx.__enter__()
    results = [None] * len(bits)
    threads = []
    try:
        t0 = time.perf_counter() + 0.2
        for k, bit in enumerate(bits):
            i = b0 + k
            trial = f"{PHASE}-{split}-{arm}-{i:04d}"
            slot = t0 + k * period
            sleep_until(slot)
            if bit:
                th = threading.Thread(target=T.chat, args=(ca, "tenant-a", T.words(f"{trial}-S", SENDER_WORDS), trial, "sender"))
                th.start(); threads.append(th)
            sleep_until(slot + PROBE_LEAD_S)

            def probe(k=k, i=i, bit=bit, trial=trial, slot=slot):
                try:
                    r = T.chat(cb, "tenant-b", T.words(f"{trial}-R", PROBE_WORDS), trial, "receiver")
                except Exception as e:  # recorded as a failed probe, kept in the data
                    r = {"status": -1, "ms": None, "wall_ns": time.time_ns(), "error": repr(e)[:200]}
                results[k] = {"phase": PHASE, "split": split, "arm": arm, "i": i, "bit": bit, "trial": trial,
                              "period_s": period, "slot_offset_s": slot - t0, "receiver": r}
            th = threading.Thread(target=probe)
            th.start(); threads.append(th)
        for th in threads:
            th.join()
    finally:
        if ctx:
            ctx.__exit__()
    for rec in results:
        out.write(json.dumps(rec) + "\n")
    out.flush()


def run(split):
    out_path = T.DATA / f"trials-{PHASE}-{split}.jsonl"
    assert not out_path.exists(), f"{out_path} exists; a split is collected once"
    if split == "holdout":
        assert (T.DATA / f"thresholds-{PHASE}.json").exists(), "freeze thresholds before holdout"
    lim = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    ca = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=120, limits=lim)
    cb = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=120, limits=lim)
    for i in range(10):
        T.chat(ca, "tenant-a", T.words(f"warm3-a-{i}", 50), f"warm3-{i}", "warmup")
        T.chat(cb, "tenant-b", T.words(f"warm3-b-{i}", 50), f"warm3-{i}", "warmup")
    with out_path.open("a") as out:
        for arm, b0, bits in schedule(split):
            run_block(split, arm, b0, bits, ca, cb, out)
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
               "trials_s3_py_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (T.DATA / f"thresholds-{PHASE}.json").write_text(json.dumps(payload, indent=1))
    print(json.dumps(payload, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "freeze"])
    ap.add_argument("--split", choices=list(N))
    a = ap.parse_args()
    run(a.split) if a.cmd == "run" else freeze()
