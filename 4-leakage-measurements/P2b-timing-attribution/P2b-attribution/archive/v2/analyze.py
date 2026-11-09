#!/usr/bin/env python3
"""P2b analysis. Runs on the Mac.

    python3 analyze.py freeze  DATA   # calibration only; writes DECODERS-FROZEN.json
    python3 analyze.py holdout DATA   # refuses unless the freeze and seals match

Statistic S = median elapsed_ms of the receiver's 15 timed probes (post.timed.*).
Threshold = midpoint of the two calibration class medians; direction from their order.
"""
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ARMS = ("shared", "pergw", "pad", "aa")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return [json.loads(line) for line in Path(path).open()]


def statistic(trial):
    timed = [r["elapsed_ms"] for r in trial["post"] if str(r.get("probe", "")).startswith("post.timed.")]
    if len(timed) != 15 or any(v is None for v in timed):
        raise ValueError(f"{trial['trial_id']}: expected 15 timed probes")
    return statistics.median(timed)


def h2(p):
    return 0.0 if p <= 0 or p >= 1 else -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out, running = [1.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        out[i] = running
    return out


def freeze(data):
    decoders = {}
    for arm in ARMS:
        path = data / arm / "calibration.jsonl"
        trials = load(path)
        s1 = [statistic(t) for t in trials if t["label"] == 1]
        s0 = [statistic(t) for t in trials if t["label"] == 0]
        m1, m0 = statistics.median(s1), statistics.median(s0)
        decoders[arm] = {"threshold_ms": (m1 + m0) / 2, "active_is_below": m1 < m0,
                         "calibration_median_active_ms": m1, "calibration_median_idle_ms": m0,
                         "calibration_trials": len(trials), "calibration_sha256": sha(path)}
    out = {"frozen_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
           "decoders": decoders, "sha256": {"analyze.py": sha(__file__), "p2b.py": sha(HERE / "p2b.py"),
                                            "audit.py": sha(HERE / "audit.py"),
                                            "PROTOCOL.md": sha(HERE / "PROTOCOL.md")}}
    (HERE / "DECODERS-FROZEN.json").write_text(json.dumps(out, indent=2))
    return out


def decode(dec, s):
    return int(s < dec["threshold_ms"]) if dec["active_is_below"] else int(s > dec["threshold_ms"])


def holdout(data):
    from scipy.stats import binomtest, fisher_exact
    frozen = json.loads((HERE / "DECODERS-FROZEN.json").read_text())
    if frozen["sha256"]["analyze.py"] != sha(__file__):
        raise SystemExit("analyze.py changed after the freeze")
    audit = json.loads((data / "audit-holdout.json").read_text())
    if not audit["passed"]:
        raise SystemExit("holdout native audit failed")
    result, ps = {}, []
    for arm in ARMS:
        path = data / arm / "holdout.jsonl"
        sealed = json.loads((data / arm / "holdout-sealed.json").read_text())
        if not sealed.get("complete") or sealed["sha256"] != sha(path):
            raise SystemExit(f"{arm}: holdout does not match its seal")
        trials = load(path)
        dec = frozen["decoders"][arm]
        y = [t["label"] for t in trials]
        s = [statistic(t) for t in trials]
        pred = [decode(dec, v) for v in s]
        correct = sum(int(a == b) for a, b in zip(pred, y))
        n = len(trials)
        acc = correct / n
        p = binomtest(correct, n, 0.5, alternative="greater").pvalue
        ps.append(p)
        probes = [r["elapsed_ms"] for t in trials for r in t["post"] if str(r.get("probe", "")).startswith("post.timed.")]
        probes.sort()
        result[arm] = {"trials": n, "correct": correct, "accuracy": acc, "p_one_sided": p,
                       "median_S_active_ms": statistics.median([v for v, l in zip(s, y) if l == 1]),
                       "median_S_idle_ms": statistics.median([v for v, l in zip(s, y) if l == 0]),
                       "probe_latency_median_ms": probes[len(probes) // 2],
                       "probe_latency_p95_ms": probes[int(0.95 * (len(probes) - 1))],
                       "failed_trials": sum(1 for t in trials if t["failed_request_markers"])}
    for arm, q in zip(ARMS, holm(ps)):
        result[arm]["p_holm"] = q
    comparisons = {}
    for arm in ("pergw", "pad"):
        a, b = result["shared"], result[arm]
        table = [[a["correct"], a["trials"] - a["correct"]], [b["correct"], b["trials"] - b["correct"]]]
        comparisons[f"shared_vs_{arm}"] = {"fisher_p_two_sided": fisher_exact(table).pvalue,
                                           "accuracy_difference": a["accuracy"] - b["accuracy"]}
    comparisons["pad_latency_cost_median_ms"] = result["pad"]["probe_latency_median_ms"] - result["shared"]["probe_latency_median_ms"]
    out = {"arms": result, "comparisons": comparisons, "freeze_sha256": sha(HERE / "DECODERS-FROZEN.json"),
           "audit_sha256": sha(data / "audit-holdout.json"),
           "scope": "Fixed-decoder accuracy for one sender protocol; not a channel-capacity estimate."}
    (HERE / "results.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    mode, data = sys.argv[1], Path(sys.argv[2])
    print(json.dumps(freeze(data) if mode == "freeze" else holdout(data), indent=2))
