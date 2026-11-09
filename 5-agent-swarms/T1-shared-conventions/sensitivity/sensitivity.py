"""Post-hoc sensitivity analyses for T1, added 2026-11-09 after reading reports/LITERATURE-REVIEW.md.

Not part of the frozen T1 protocol. Reads the frozen data and scorer; writes only here.
1. One-to-one pairing: within each cell, sender sample i pairs only with receiver sample i
   (collection order), so no response is reused inside a cell. Bootstrap resamples i.
2. Model-level test: each model contributes one number (own rate minus its mean rate with
   other developers' models, both directions); exact sign test over the 7 models.
"""
import collections, itertools, json, sys
from pathlib import Path
import numpy as np
from scipy.stats import binomtest

T = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(T))
import score as S  # noqa: E402
from collect import MODELS  # noqa: E402

DEV = {m: v[1] for m, v in MODELS.items()}
snd, rcv = collections.defaultdict(dict), collections.defaultdict(dict)
for line in (T / "main" / "calls.jsonl").read_text().splitlines():
    e = json.loads(line)
    key = (e["model"], e["wording"], e["context"])
    if e["role"] == "sender":
        snd[key][e["idx"]] = S.parse_sender(e["text"])
    else:
        rcv[key][e["idx"]] = S.parse_receiver(e["text"])[0]
N = 24
WP = {"same": [("W1", "W1"), ("W2", "W2")], "cross": [("W1", "W2"), ("W2", "W1")]}
cells = {}
for c in ("C0", "C1"):
    for a, b in itertools.product(MODELS, MODELS):
        for ws, wr in itertools.product(("W1", "W2"), repeat=2):
            cells[(a, b, ws, wr, c)] = np.array([S.full(snd[(a, ws, c)][i], rcv[(b, wr, c)][i]) for i in range(N)], float)


def cls(a, b):
    return "same_model" if a == b else ("same_developer" if DEV[a] == DEV[b] else "cross_developer")


def est(idx):
    out = {}
    for wt, wps in WP.items():
        per = collections.defaultdict(list)
        for (a, b, ws, wr, c), v in cells.items():
            if (ws, wr) in wps:
                per[(a, b)].append(v[idx].mean())
        byc = collections.defaultdict(list)
        for (a, b), vals in per.items():
            byc[cls(a, b)].append(np.mean(vals))
        for k, v in byc.items():
            out[f"{wt}|{k}"] = float(np.mean(v))
        out[f"{wt}|diff"] = out[f"{wt}|same_model"] - out[f"{wt}|cross_developer"]
    return out


point = est(np.arange(N))
rng = np.random.default_rng(20261109)
boots = collections.defaultdict(list)
for _ in range(5000):
    for k, v in est(rng.integers(0, N, N)).items():
        boots[k].append(v)
one_to_one = {k: {"estimate": v, "ci95": [float(np.percentile(boots[k], 2.5)), float(np.percentile(boots[k], 97.5))]}
              for k, v in point.items()}

frozen = json.loads((T / "main" / "analysis.json").read_text())["matrix_full"]
model_level = {}
for wt in ("same", "cross"):
    m = frozen[f"{wt}|pooled"]
    diffs = {}
    for x in MODELS:
        cross = [m[f"{x}->{y}"] for y in MODELS if DEV[y] != DEV[x]] + [m[f"{y}->{x}"] for y in MODELS if DEV[y] != DEV[x]]
        diffs[x] = m[f"{x}->{x}"] - float(np.mean(cross))
    pos = sum(d > 0 for d in diffs.values())
    model_level[wt] = {"per_model_own_minus_cross": diffs, "positive": pos, "n": len(diffs),
                       "sign_test_one_sided_p": binomtest(pos, len(diffs), 0.5, alternative="greater").pvalue}
out = {"note": "post-hoc sensitivity; not the frozen primary analysis", "one_to_one": one_to_one, "model_level": model_level}
Path(__file__).with_name("sensitivity.json").write_text(json.dumps(out, indent=1))
for k, v in one_to_one.items():
    print(f"{k:28s} {v['estimate']:.3f}  ({v['ci95'][0]:.3f}-{v['ci95'][1]:.3f})")
for wt, r in model_level.items():
    print(wt, "models with own>cross:", r["positive"], "/", r["n"], "sign p =", round(r["sign_test_one_sided_p"], 4))
