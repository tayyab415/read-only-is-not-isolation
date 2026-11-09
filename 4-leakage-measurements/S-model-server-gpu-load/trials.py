#!/usr/bin/env python3
"""S1 scripted sender/receiver trials through the tenant gateways.

Tenant A (sender) and tenant B (receiver) use different keys and separate HTTP clients.
The receiver decides from its own measured latency only. Prompts are synthetic random
words, fresh per trial, so only the sender could have put the receiver's prefix in cache.

  trials.py run   --phase cache_on  --split calibration|holdout
  trials.py run   --phase cache_off --split calibration|holdout
  trials.py freeze --phase ...      (fit thresholds on calibration, write thresholds-<phase>.json)
"""
import argparse, hashlib, json, random, string, threading, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KEYS = json.loads((Path.home() / "s1" / "tenant-keys.json").read_text())
KEY_OF = {t: k for k, t in KEYS.items()}
MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
PREFIX_WORDS = 400       # about 1,000 prompt tokens; checked in smoke from the gateway log
HEAVY_WORDS = 2400       # contention sender, about 6,000 tokens
BUSY_WORKERS = 4         # background tenant-c threads during *_busy blocks
BUSY_WORDS = 400
GAP_S = 0.05             # sender response complete -> receiver request
LOAD_LEAD_S = 0.010      # contention: sender request start -> receiver request
INTER_TRIAL_S = 0.10
N = {"calibration": 120, "holdout": 400}
BLOCK = 20
PHASES = {
    "cache_on": {
        "shared":      {"port": 9001, "mode": "cache"},
        "salted":      {"port": 9002, "mode": "cache"},
        "client_salt": {"port": 9003, "mode": "cache", "client_salt": "lab-shared-salt"},
        "contention":  {"port": 9002, "mode": "load"},
        "contention_light": {"port": 9002, "mode": "load", "heavy_words": 400},
        "shared_short": {"port": 9001, "mode": "cache", "words": 40},
        "shared_busy": {"port": 9001, "mode": "cache", "busy": True},
        "salted_busy": {"port": 9002, "mode": "cache", "busy": True},
    },
    "cache_off": {
        "nocache":     {"port": 9001, "mode": "cache"},
    },
}


def words(seed, n):
    rng = random.Random(seed)
    return " ".join("".join(rng.choice(string.ascii_lowercase) for _ in range(4)) for _ in range(n))


def chat(client, tenant, text, trial, role, salt=None, max_tokens=1):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0}
    if salt:
        body["cache_salt"] = salt
    t0 = time.perf_counter_ns()
    r = client.post("/v1/chat/completions", json=body,
                    headers={"authorization": f"Bearer {KEY_OF[tenant]}", "x-trial-id": trial, "x-role": role})
    t1 = time.perf_counter_ns()
    return {"status": r.status_code, "ms": (t1 - t0) / 1e6, "wall_ns": time.time_ns()}


class Busy:
    """Background traffic from tenant-c (ordinary 1,000-token prompts, 32 output tokens)."""

    def __init__(self, port, tag):
        self.stop, self.port, self.tag, self.n = threading.Event(), port, tag, 0
        self.threads = [threading.Thread(target=self.loop, args=(k,)) for k in range(BUSY_WORKERS)]

    def loop(self, k):
        c = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=120)
        j = 0
        while not self.stop.is_set():
            chat(c, "tenant-c", words(f"{self.tag}-bg{k}-{j}", BUSY_WORDS), f"{self.tag}-bg", "background", max_tokens=32)
            j += 1
            self.n += 1

    def __enter__(self):
        for t in self.threads:
            t.start()
        time.sleep(0.5)
        return self

    def __exit__(self, *exc):
        self.stop.set()
        for t in self.threads:
            t.join()


def schedule(phase, split):
    """Balanced bits per arm, arms interleaved in shuffled blocks of BLOCK trials."""
    rng = random.Random(f"S1-{phase}-{split}")
    blocks = []
    for arm in PHASES[phase]:
        bits = [1] * (N[split] // 2) + [0] * (N[split] // 2)
        rng.shuffle(bits)
        for b0 in range(0, len(bits), BLOCK):
            blocks.append([(arm, b0 + i, bit) for i, bit in enumerate(bits[b0:b0 + BLOCK])])
    rng.shuffle(blocks)
    return [t for blk in blocks for t in blk]


def one_trial(phase, split, arm, i, bit, clients):
    cfg = PHASES[phase][arm]
    ca, cb = clients[cfg["port"]]
    trial = f"{phase}-{split}-{arm}-{i:04d}"
    salt = cfg.get("client_salt")
    nwords = cfg.get("words", PREFIX_WORDS)
    rec = {"phase": phase, "split": split, "arm": arm, "i": i, "bit": bit, "trial": trial}
    if cfg["mode"] == "cache":
        p_text = words(f"{trial}-P", nwords)
        q_text = words(f"{trial}-Q", nwords)
        rec["sender"] = chat(ca, "tenant-a", p_text if bit else q_text, trial, "sender", salt)
        time.sleep(GAP_S)
        rec["receiver"] = chat(cb, "tenant-b", p_text + "\nReply with one word.", trial, "receiver", salt)
    else:
        holder, th = {}, None
        if bit:
            heavy = words(f"{trial}-H", cfg.get("heavy_words", HEAVY_WORDS))
            th = threading.Thread(target=lambda: holder.update(s=chat(ca, "tenant-a", heavy, trial, "sender")))
            th.start()
        time.sleep(LOAD_LEAD_S)
        rec["receiver"] = chat(cb, "tenant-b", words(f"{trial}-R", PREFIX_WORDS), trial, "receiver")
        if th:
            th.join()
        rec["sender"] = holder.get("s")
    return rec


def run(phase, split):
    DATA.mkdir(exist_ok=True)
    out = DATA / f"trials-{phase}-{split}.jsonl"
    assert not out.exists(), f"{out} exists; a split is collected once"
    if split == "holdout":
        assert (DATA / f"thresholds-{phase}.json").exists(), "freeze thresholds before holdout"
    clients = {p: (httpx.Client(base_url=f"http://127.0.0.1:{p}", timeout=120),
                   httpx.Client(base_url=f"http://127.0.0.1:{p}", timeout=120))
               for p in {cfg["port"] for cfg in PHASES[phase].values()}}
    # Warm-up, not recorded as trials: 10 requests per tenant per gateway.
    for p, (ca, cb) in clients.items():
        for i in range(10):
            chat(ca, "tenant-a", words(f"warm-a-{p}-{i}", 50), f"warm-{p}-{i}", "warmup")
            chat(cb, "tenant-b", words(f"warm-b-{p}-{i}", 50), f"warm-{p}-{i}", "warmup")
    sched = schedule(phase, split)
    blocks = [sched[k:k + BLOCK] for k in range(0, len(sched), BLOCK)]
    with out.open("a") as f:
        for blk in blocks:
            arm = blk[0][0]
            cfg = PHASES[phase][arm]
            ctx = Busy(cfg["port"], f"{phase}-{split}-{arm}-{blk[0][1]:04d}") if cfg.get("busy") else None
            if ctx:
                ctx.__enter__()
            try:
                for arm, i, bit in blk:
                    f.write(json.dumps(one_trial(phase, split, arm, i, bit, clients)) + "\n")
                    f.flush()
                    time.sleep(INTER_TRIAL_S)
            finally:
                if ctx:
                    ctx.__exit__()
    print("wrote", out, len(sched), "trials")


def fit_threshold(lat, bits, low_means_one):
    """Threshold maximising calibration accuracy (first maximum over sorted midpoints)."""
    pts = sorted(set(lat))
    cands = [(a + b) / 2 for a, b in zip(pts, pts[1:])] or pts
    best, best_acc = None, -1
    for c in cands:
        acc = sum((x < c) == bool(b) if low_means_one else (x > c) == bool(b) for x, b in zip(lat, bits)) / len(bits)
        if acc > best_acc:
            best, best_acc = c, acc
    return best, best_acc


def freeze(phase):
    rows = [json.loads(l) for l in (DATA / f"trials-{phase}-calibration.jsonl").read_text().splitlines()]
    th = {}
    for arm, cfg in PHASES[phase].items():
        rs = [r for r in rows if r["arm"] == arm and r["receiver"]["status"] == 200]
        lat, bits = [r["receiver"]["ms"] for r in rs], [r["bit"] for r in rs]
        low = cfg["mode"] == "cache"   # cache hit -> faster; contention -> slower
        t, acc = fit_threshold(lat, bits, low)
        th[arm] = {"threshold_ms": t, "low_means_one": low, "calibration_accuracy": acc, "n": len(rs)}
    src = DATA / f"trials-{phase}-calibration.jsonl"
    payload = {"phase": phase, "thresholds": th, "frozen_utc_ns": time.time_ns(),
               "calibration_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
               "trials_py_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (DATA / f"thresholds-{phase}.json").write_text(json.dumps(payload, indent=1))
    print(json.dumps(payload, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "freeze"])
    ap.add_argument("--phase", required=True, choices=list(PHASES))
    ap.add_argument("--split", choices=list(N))
    a = ap.parse_args()
    run(a.phase, a.split) if a.cmd == "run" else freeze(a.phase)
