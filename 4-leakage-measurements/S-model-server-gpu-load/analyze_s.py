#!/usr/bin/env python3
"""S1 frozen analysis. Reads data/ (trials, thresholds, utility) and logs/ (gateway JSONL).

Per arm, holdout only: accuracy of the frozen threshold with a Wilson 95% interval and an
exact two-sided binomial p against 0.5; threshold-free AUC with a bootstrap 95% interval;
a capacity bound 1 - H2(lower Wilson bound) bits per trial (0 if the bound is <= 0.5).
Native audit: every trial's sender and receiver requests appear once in the gateway log
with the expected tenant and policy; receiver cached tokens by bit.
"""
import collections, json, math, sys
from pathlib import Path
import numpy as np
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parent
DATA, LOGS = ROOT / "data", ROOT / "logs"
POLICY_OF_PORT = {9001: "shared", 9002: "salted", 9003: "passthrough"}
sys.path.insert(0, str(ROOT))


def wilson(k, n, z=1.959964):
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [(c - h) / d, (c + h) / d]


def h2(p):
    return 0.0 if p in (0, 1) else -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def auc(scores, labels):
    """P(score of a 1-trial > score of a 0-trial), ties 0.5."""
    s1 = np.array([s for s, l in zip(scores, labels) if l == 1])
    s0 = np.array([s for s, l in zip(scores, labels) if l == 0])
    gt = (s1[:, None] > s0[None, :]).mean()
    eq = (s1[:, None] == s0[None, :]).mean()
    return float(gt + 0.5 * eq)


def load_gateway(phase):
    by_trial = collections.defaultdict(list)
    for f in LOGS.glob(f"gw-{phase}-*.jsonl"):
        for line in f.read_text().splitlines():
            d = json.loads(line)
            if d.get("trial"):
                by_trial[d["trial"]].append(d)
    return by_trial


def arm_report(phase, arm, cfg, rows, th, gw):
    rs = [r for r in rows if r["arm"] == arm]
    ok = [r for r in rs if r["receiver"]["status"] == 200]
    lat = [r["receiver"]["ms"] for r in ok]
    bits = [r["bit"] for r in ok]
    t = th["thresholds"][arm]
    pred = [int(x < t["threshold_ms"]) if t["low_means_one"] else int(x > t["threshold_ms"]) for x in lat]
    k = sum(int(p == b) for p, b in zip(pred, bits))
    n = len(ok)
    lo, hi = wilson(k, n)
    score = [-x for x in lat] if t["low_means_one"] else lat
    rng = np.random.default_rng(7)
    idx1 = [i for i, b in enumerate(bits) if b == 1]
    idx0 = [i for i, b in enumerate(bits) if b == 0]
    boots = []
    for _ in range(2000):
        a = rng.choice(idx1, len(idx1)); b = rng.choice(idx0, len(idx0))
        ii = list(a) + list(b)
        boots.append(auc([score[i] for i in ii], [bits[i] for i in ii]))
    walls = sorted(r["receiver"]["wall_ns"] for r in ok)
    # Audit against the gateway log.
    expected_policy = POLICY_OF_PORT[cfg["port"]]
    audit = collections.Counter()
    cached = collections.defaultdict(list)
    for r in rs:
        recs = gw.get(r["trial"], [])
        roles = collections.Counter(d["role"] for d in recs)
        want_sender = 1 if (cfg["mode"] == "cache" or r["bit"] == 1) else 0
        audit["trials"] += 1
        audit["receiver_once"] += roles["receiver"] == 1
        audit["sender_as_expected"] += roles["sender"] == want_sender
        for d in recs:
            audit["records"] += 1
            audit["tenant_ok"] += d["tenant"] == {"sender": "tenant-a", "receiver": "tenant-b"}[d["role"]]
            audit["policy_ok"] += d["policy"] == expected_policy
            salted_expected = expected_policy == "salted" or (expected_policy == "passthrough" and "client_salt" in cfg)
            audit["salt_ok"] += (d["salt_sha256_12"] is not None) == salted_expected
            if d["role"] == "receiver":
                cached[r["bit"]].append(d["cached_tokens"])
    return {
        "n_holdout": n, "failed_receiver": len(rs) - n, "threshold": t,
        "accuracy": k / n, "wilson95": [lo, hi], "binom_p_two_sided": binomtest(k, n, 0.5).pvalue,
        "auc": auc(score, bits), "auc_boot95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
        "median_ms": {b: float(np.median([x for x, y in zip(lat, bits) if y == b])) for b in (0, 1)},
        "capacity_lower_bits_per_trial": (1 - h2(lo)) if lo > 0.5 else 0.0,
        "trials_per_s": (n - 1) / ((walls[-1] - walls[0]) / 1e9) if n > 1 else None,
        "receiver_cached_tokens_median": {b: (float(np.median(v)) if v and None not in v else None) for b, v in cached.items()},
        "receiver_cross_tenant_hit_frac": {b: float(np.mean([c > 64 for c in v])) for b, v in cached.items() if v and None not in v},
        "audit": dict(audit),
    }


def utility_report(phase, gw_all):
    f = DATA / f"utility-{phase}.json"
    if not f.exists():
        return None
    out = collections.defaultdict(list)
    for row in json.loads(f.read_text()):
        req = row["requests"]
        ok = [r for r in req if r["status"] == 200]
        logs = gw_all.get(row["tag"], [])
        pt = sum(d["prompt_tokens"] or 0 for d in logs)
        ct = sum(d["cached_tokens"] or 0 for d in logs)
        out[(row["config"], row["workload"])].append({
            "ok": len(ok), "n": len(req), "mean_ms": float(np.mean([r["ms"] for r in ok])),
            "p95_ms": float(np.percentile([r["ms"] for r in ok], 95)), "req_per_s": len(ok) / row["wall_s"],
            "cache_hit_frac": ct / pt if pt else None, "log_records": len(logs)})
    rep = {}
    for k, v in out.items():
        rep[f"{k[0]}|{k[1]}"] = {m: {"mean": float(np.mean([x[m] for x in v])), "sd": float(np.std([x[m] for x in v], ddof=1)) if len(v) > 1 else None}
                                 for m in ("mean_ms", "p95_ms", "req_per_s", "cache_hit_frac")}
        rep[f"{k[0]}|{k[1]}"]["reps"] = v
    return rep


def main():
    from trials import PHASES
    report = {}
    for phase in PHASES:
        thf = DATA / f"thresholds-{phase}.json"
        hof = DATA / f"trials-{phase}-holdout.jsonl"
        gw = load_gateway(phase)
        if thf.exists() and hof.exists():
            th = json.loads(thf.read_text())
            rows = [json.loads(l) for l in hof.read_text().splitlines()]
            for arm, cfg in PHASES[phase].items():
                report[arm] = arm_report(phase, arm, cfg, rows, th, gw)
        u = utility_report(phase, gw)
        if u:
            report[f"utility|{phase}"] = u
    (ROOT / "analysis.json").write_text(json.dumps(report, indent=1, default=str))
    for arm, r in report.items():
        if arm.startswith("utility"):
            for k, v in r.items():
                print(arm, k, {m: round(v[m]["mean"], 3) if v[m]["mean"] is not None else None for m in ("mean_ms", "p95_ms", "req_per_s", "cache_hit_frac")})
        else:
            print(f"{arm:16s} acc={r['accuracy']:.3f} wilson={[round(x, 3) for x in r['wilson95']]} auc={r['auc']:.3f} "
                  f"med={ {b: round(m, 1) for b, m in r['median_ms'].items()} } cap_lo={r['capacity_lower_bits_per_trial']:.3f} "
                  f"cached={r['receiver_cached_tokens_median']} audit={r['audit']}")


if __name__ == "__main__":
    main()
