#!/usr/bin/env python3
"""S4 frozen analysis: separate model-server processes on one GPU."""
import collections, json, sys
from pathlib import Path
import numpy as np
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from analyze_s import auc, h2, wilson  # noqa: E402

DATA, LOGS = ROOT / "data", ROOT / "logs"


def main():
    th = json.loads((DATA / "thresholds-s4.json").read_text())["thresholds"]
    rows = [json.loads(l) for l in (DATA / "trials-s4-holdout.jsonl").read_text().splitlines()]
    logs = {}
    for side in ("a", "b"):
        g = collections.defaultdict(list)
        for line in (LOGS / f"gw-s4-{side}.jsonl").read_text().splitlines():
            d = json.loads(line)
            if d.get("trial"):
                g[d["trial"]].append(d)
        logs[side] = g
    out = {}
    for arm, t in th.items():
        rs = [r for r in rows if r and r["arm"] == arm]
        ok = [r for r in rs if r["receiver"]["status"] == 200]
        lat, bits = [r["receiver"]["ms"] for r in ok], [r["bit"] for r in ok]
        k = sum(int((x > t["threshold_ms"]) == bool(b)) for x, b in zip(lat, bits))
        n = len(rs)
        lo, hi = wilson(k, n)
        period = rs[0]["period_s"]
        cap_pt = (1 - h2(k / n)) if k / n > 0.5 else 0.0
        cap_lo = (1 - h2(lo)) if lo > 0.5 else 0.0
        audit = collections.Counter()
        for r in rs:
            a, b = logs["a"].get(r["trial"], []), logs["b"].get(r["trial"], [])
            audit["trials"] += 1
            audit["receiver_once_in_b"] += sum(d["role"] == "receiver" for d in b) == 1
            audit["sender_in_a_as_expected"] += sum(d["role"] == "sender" for d in a) == r["bit"]
            audit["no_crossing"] += all(d["tenant"] == "tenant-a" for d in a) and all(d["tenant"] == "tenant-b" for d in b)
        out[arm] = {"period_s": period, "n": n, "failed_probes": n - len(ok), "accuracy": k / n, "wilson95": [lo, hi],
                    "binom_p": binomtest(k, n, 0.5).pvalue, "auc": auc(lat, bits),
                    "median_ms": {b: float(np.median([x for x, y in zip(lat, bits) if y == b])) for b in (0, 1)},
                    "bits_per_symbol_point": cap_pt, "bits_per_symbol_lower": cap_lo,
                    "bits_per_second_point": cap_pt / period, "bits_per_second_lower": cap_lo / period,
                    "audit": dict(audit)}
    (ROOT / "analysis-s4.json").write_text(json.dumps(out, indent=1))
    for arm, r in out.items():
        print(f"{arm:9s} acc={r['accuracy']:.3f} wilson={[round(x, 3) for x in r['wilson95']]} auc={r['auc']:.3f} "
              f"med={ {b: round(m, 1) for b, m in r['median_ms'].items()} } bits/s point={r['bits_per_second_point']:.2f} "
              f"lower={r['bits_per_second_lower']:.2f} audit={r['audit']}")


if __name__ == "__main__":
    main()
