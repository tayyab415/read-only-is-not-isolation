#!/usr/bin/env python3
"""S3 frozen analysis: per-arm holdout accuracy and achievable bits per second."""
import collections, json, sys
from pathlib import Path
import numpy as np
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from analyze_s import auc, h2, wilson  # noqa: E402

DATA, LOGS = ROOT / "data", ROOT / "logs"


def main():
    th = json.loads((DATA / "thresholds-s3.json").read_text())["thresholds"]
    rows = [json.loads(l) for l in (DATA / "trials-s3-holdout.jsonl").read_text().splitlines()]
    gw = collections.defaultdict(list)
    for line in (LOGS / "gw-s3-salted.jsonl").read_text().splitlines():
        d = json.loads(line)
        if d.get("trial"):
            gw[d["trial"]].append(d)
    out = {}
    for arm, t in th.items():
        rs = [r for r in rows if r and r["arm"] == arm]
        ok = [r for r in rs if r["receiver"]["status"] == 200]
        lat, bits = [r["receiver"]["ms"] for r in ok], [r["bit"] for r in ok]
        k = sum(int((x > t["threshold_ms"]) == bool(b)) for x, b in zip(lat, bits))
        n = len(rs)  # failed probes count as errors
        lo, hi = wilson(k, n)
        period = rs[0]["period_s"]
        cap_pt = (1 - h2(k / n)) if k / n > 0.5 else 0.0
        cap_lo = (1 - h2(lo)) if lo > 0.5 else 0.0
        audit = collections.Counter()
        for r in rs:
            recs = gw.get(r["trial"], [])
            roles = collections.Counter(d["role"] for d in recs)
            audit["trials"] += 1
            audit["receiver_once"] += roles["receiver"] == 1
            audit["sender_as_expected"] += roles["sender"] == r["bit"]
            audit["tenant_ok"] += all(d["tenant"] == {"sender": "tenant-a", "receiver": "tenant-b"}[d["role"]] for d in recs)
            audit["salted"] += all(d["salt_sha256_12"] is not None for d in recs)
        out[arm] = {"period_s": period, "n": n, "failed_probes": n - len(ok), "accuracy": k / n, "wilson95": [lo, hi],
                    "binom_p": binomtest(k, n, 0.5).pvalue, "auc": auc(lat, bits),
                    "median_ms": {b: float(np.median([x for x, y in zip(lat, bits) if y == b])) for b in (0, 1)},
                    "bits_per_symbol_point": cap_pt, "bits_per_symbol_lower": cap_lo,
                    "bits_per_second_point": cap_pt / period, "bits_per_second_lower": cap_lo / period,
                    "audit": dict(audit)}
    (ROOT / "analysis-s3.json").write_text(json.dumps(out, indent=1))
    for arm, r in out.items():
        print(f"{arm:10s} T={r['period_s']*1000:.0f}ms acc={r['accuracy']:.3f} wilson={[round(x, 3) for x in r['wilson95']]} "
              f"auc={r['auc']:.3f} med={ {b: round(m, 1) for b, m in r['median_ms'].items()} } "
              f"bits/s point={r['bits_per_second_point']:.2f} lower={r['bits_per_second_lower']:.2f} fail={r['failed_probes']} audit={r['audit']}")


if __name__ == "__main__":
    main()
