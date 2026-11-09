#!/usr/bin/env python3
"""T2 frozen analysis: C1 vs C2 convention agreement (W1/W1 pairs)."""
import collections, json, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
T1 = ROOT.parent / "T-twins"
sys.path.insert(0, str(T1))
import score as S  # noqa: E402
from collect import MODELS  # noqa: E402

DEV = {m: v[1] for m, v in MODELS.items()}
NAMES = ["s3ekiw.bin", "t655ot.bin", "n28cha.bin", "ikj3w9.bin", "tfl0ez.bin", "q10abc.bin", "oj1es9.bin", "gdhht9.bin",
         "2dwk1o.bin", "ymvxo7.bin", "znos82.bin", "6g2lao.bin", "pwcqkh.bin", "fl3guo.bin", "okiek2.bin", "0trkjp.bin"]


def cls(a, b):
    return "same_model" if a == b else ("same_developer" if DEV[a] == DEV[b] else "cross_developer")


def load(path):
    snd, rcv = collections.defaultdict(list), collections.defaultdict(list)
    for line in Path(path).read_text().splitlines():
        e = json.loads(line)
        if e["event"] not in ("completed", "failed"):
            continue
        key = (e["model"], e["context"])
        if e["role"] == "sender":
            snd[key].append(S.parse_sender(e["text"]) if e["event"] == "completed" else None)
        else:
            rcv[key].append(S.parse_receiver(e["text"])[0] if e["event"] == "completed" else None)
    return snd, rcv


def mats(snd, rcv, contexts):
    M = {}
    for c in contexts:
        for a in MODELS:
            for b in MODELS:
                ss, rr = snd[(a, c)], rcv[(b, c)]
                M[(a, b, c)] = np.array([[S.full(s, r) for r in rr] for s in ss], float)
    return M


def est(M, idx, c):
    by = collections.defaultdict(list)
    for (a, b, cc), m in M.items():
        if cc != c:
            continue
        x = m if idx is None else m[np.ix_(idx[(a, c, "s")], idx[(b, c, "r")])]
        by[cls(a, b)].append(x.mean())
    return {k: float(np.mean(v)) for k, v in by.items()}


def main():
    snd, rcv = load(ROOT / "main" / "calls.jsonl")
    M = mats(snd, rcv, ("C1", "C2"))
    rng = np.random.default_rng(20261109)
    pt = {c: est(M, None, c) for c in ("C1", "C2")}
    boots = collections.defaultdict(list)
    for _ in range(10000):
        idx = {}
        for (m, c), lst in snd.items():
            idx[(m, c, "s")] = rng.integers(0, len(lst), len(lst))
        for (m, c), lst in rcv.items():
            idx[(m, c, "r")] = rng.integers(0, len(lst), len(lst))
        e1, e2 = est(M, idx, "C1"), est(M, idx, "C2")
        for k in e1:
            boots[f"C1|{k}"].append(e1[k]); boots[f"C2|{k}"].append(e2[k]); boots[f"C2-C1|{k}"].append(e2[k] - e1[k])
    out = {"rates": {}}
    for key, b in boots.items():
        c, k = key.split("|")
        point = pt[c][k] if c in pt else pt["C2"][k] - pt["C1"][k]
        out["rates"][key] = {"estimate": point, "ci95": [float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))]}
    # per-model own rates and C2 sender shapes
    out["own"] = {c: {m: float(M[(m, m, c)].mean()) for m in MODELS} for c in ("C1", "C2")}
    shapes = collections.Counter()
    for m in MODELS:
        for t in snd[(m, "C2")]:
            if not t:
                shapes[(m, "invalid")] += 1; continue
            p7 = S.norm(t["7"])
            if p7 and p7.endswith(NAMES[7]):
                shapes[(m, "nth_listed")] += 1
            elif p7 and p7.endswith(sorted(NAMES)[7]):
                shapes[(m, "nth_sorted")] += 1
            else:
                shapes[(m, "other")] += 1
    out["c2_sender_value7_shape"] = {f"{m}|{k}": v for (m, k), v in sorted(shapes.items())}
    # descriptive: T1 C1/W1 cells for comparison (different collection time)
    t1 = json.loads((T1 / "main" / "analysis.json").read_text())["matrix_full"]
    out["t1_c1_w1_note"] = "T1 matrix_full same|C1 pools W1/W1 and W2/W2; shown for orientation only"
    out["t1_c1_same_wording_matrix"] = t1["same|C1"]
    (ROOT / "main" / "analysis.json").write_text(json.dumps(out, indent=1))
    for k, v in sorted(out["rates"].items()):
        print(f"{k:28s} {v['estimate']:.3f} ({v['ci95'][0]:.3f}-{v['ci95'][1]:.3f})")
    print("own C1", {m: round(v, 2) for m, v in out["own"]["C1"].items()})
    print("own C2", {m: round(v, 2) for m, v in out["own"]["C2"].items()})
    print(out["c2_sender_value7_shape"])


if __name__ == "__main__":
    main()
