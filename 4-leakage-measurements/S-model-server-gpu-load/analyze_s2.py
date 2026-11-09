#!/usr/bin/env python3
"""S2 frozen analysis: holdout decoding per arm and the floor's latency cost."""
import collections, json, sys
from pathlib import Path
import numpy as np
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from analyze_s import auc, h2, wilson  # noqa: E402

DATA, LOGS = ROOT / "data", ROOT / "logs"
ARMS = {"floor500_short": "floor500", "floor500_long": "floor500", "nofloor_long": "salted"}


def main():
    th = json.loads((DATA / "thresholds-floor.json").read_text())["thresholds"]
    rows = [json.loads(l) for l in (DATA / "trials-floor-holdout.jsonl").read_text().splitlines()]
    gw = collections.defaultdict(list)
    for name in ("gw-floor-salted.jsonl", "gw-floor-floor500.jsonl"):
        for line in (LOGS / name).read_text().splitlines():
            d = json.loads(line)
            d["_file"] = name
            if d.get("trial"):
                gw[d["trial"]].append(d)
    out = {}
    for arm, gname in ARMS.items():
        rs = [r for r in rows if r["arm"] == arm]
        ok = [r for r in rs if r["receiver"]["status"] == 200]
        lat, bits = [r["receiver"]["ms"] for r in ok], [r["bit"] for r in ok]
        t = th[arm]["threshold_ms"]
        k = sum(int((x > t) == bool(b)) for x, b in zip(lat, bits))
        n = len(ok)
        lo, hi = wilson(k, n)
        rng = np.random.default_rng(11)
        i1 = [i for i, b in enumerate(bits) if b]; i0 = [i for i, b in enumerate(bits) if not b]
        boots = []
        for _ in range(2000):
            ii = list(rng.choice(i1, len(i1))) + list(rng.choice(i0, len(i0)))
            boots.append(auc([lat[i] for i in ii], [bits[i] for i in ii]))
        audit = collections.Counter()
        for r in rs:
            recs = gw.get(r["trial"], [])
            roles = collections.Counter(d["role"] for d in recs)
            audit["trials"] += 1
            audit["receiver_once"] += roles["receiver"] == 1
            audit["sender_as_expected"] += roles["sender"] == r["bit"]
            audit["gateway_ok"] += all(d["_file"] == f"gw-floor-{gname}.jsonl" for d in recs)
            audit["tenant_ok"] += all(d["tenant"] == {"sender": "tenant-a", "receiver": "tenant-b"}[d["role"]] for d in recs)
        out[arm] = {"n": n, "failed": len(rs) - n, "threshold_ms": t, "accuracy": k / n, "wilson95": [lo, hi],
                    "binom_p": binomtest(k, n, 0.5).pvalue, "auc": auc(lat, bits),
                    "auc_boot95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                    "median_ms": {b: float(np.median([x for x, y in zip(lat, bits) if y == b])) for b in (0, 1)},
                    "capacity_lower_bits_per_trial": (1 - h2(lo)) if lo > 0.5 else 0.0, "audit": dict(audit)}
    u = json.loads((DATA / "utility-floor.json").read_text())
    util = {}
    for g in ("nofloor", "floor500"):
        for kind in ("probe_1tok", "turn_32tok", "turn_128tok"):
            v = [r["ms"] for r in u if r["gateway"] == g and r["kind"] == kind and r["status"] == 200]
            util[f"{g}|{kind}"] = {"n": len(v), "mean_ms": float(np.mean(v)), "p95_ms": float(np.percentile(v, 95))}
    out["utility"] = util
    (ROOT / "analysis-s2.json").write_text(json.dumps(out, indent=1))
    for arm in ARMS:
        r = out[arm]
        print(f"{arm:15s} acc={r['accuracy']:.3f} wilson={[round(x, 3) for x in r['wilson95']]} auc={r['auc']:.3f} "
              f"med={ {b: round(m, 1) for b, m in r['median_ms'].items()} } audit={r['audit']}")
    for k, v in util.items():
        print(k, round(v["mean_ms"], 1), round(v["p95_ms"], 1))


if __name__ == "__main__":
    main()
