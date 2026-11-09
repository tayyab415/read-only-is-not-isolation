#!/usr/bin/env python3
"""T1 frozen analysis. Usage: analyze.py <stage-dir> [--boot N]

Unit: an ordered pair (sender sample, receiver sample) in the same server context.
All sender x receiver combinations in a cell are scored, so samples are reused across
pairs; uncertainty comes from a stratified two-way bootstrap that resamples sender
samples and receiver samples independently within each (model, role, wording, context).
"""
import argparse, collections, itertools, json, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import score as S  # noqa: E402
from collect import MODELS  # noqa: E402

DEV = {m: v[1] for m, v in MODELS.items()}
WPAIRS = {"same": [("W1", "W1"), ("W2", "W2")], "cross": [("W1", "W2"), ("W2", "W1")]}
CONTEXTS = ("C0", "C1")


def load(stage_dir):
    snd, rcv, status = collections.defaultdict(list), collections.defaultdict(list), collections.Counter()
    for line in (stage_dir / "calls.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("kind") != "task" or e["event"] not in ("completed", "failed"):
            continue
        key = (e["model"], e["wording"], e["context"])
        if e["event"] == "failed":
            status[(e["model"], e["role"], "failed")] += 1
            parsed = (None, []) if e["role"] == "receiver" else None
        elif e["role"] == "sender":
            parsed = S.parse_sender(e["text"])
        else:
            parsed = S.parse_receiver(e["text"])
        ok = parsed is not None if e["role"] == "sender" else parsed[0] is not None
        if e["event"] == "completed":
            status[(e["model"], e["role"], "valid" if ok else "invalid")] += 1
        # Invalid or failed samples stay in the denominators (they match nothing).
        (snd if e["role"] == "sender" else rcv)[key].append(parsed)
    return snd, rcv, status


def matrices(snd, rcv):
    """M[(ms, mr, ws, wr, c)][metric] = nS x nR array."""
    out = {}
    for c in CONTEXTS:
        for ms, mr in itertools.product(MODELS, MODELS):
            for ws, wr in itertools.product(("W1", "W2"), repeat=2):
                ss, rr = snd.get((ms, ws, c), []), rcv.get((mr, wr, c), [])
                if not ss or not rr:
                    continue
                m = {k: np.zeros((len(ss), len(rr))) for k in ("full", "value", "lenient", "top3")}
                for i, s in enumerate(ss):
                    for j, (rp, ra) in enumerate(rr):
                        m["full"][i, j] = S.full(s, rp)
                        m["value"][i, j] = S.per_value(s, rp) / 16
                        m["lenient"][i, j] = S.full(s, rp, lenient=True)
                        m["top3"][i, j] = S.full_top3(s, rp, ra)
                out[(ms, mr, ws, wr, c)] = m
    return out


def pair_class(ms, mr):
    if ms == mr:
        return "same_model"
    return "same_developer" if DEV[ms] == DEV[mr] else "cross_developer"


def estimates(M, idx, metric, contexts=CONTEXTS):
    """Equal-weight means over models / ordered model pairs, contexts and wording pairs."""
    res = {}
    for wtype, wps in WPAIRS.items():
        by_class = collections.defaultdict(list)
        cells = {}
        for (ms, mr, ws, wr, c), m in M.items():
            if (ws, wr) not in wps or c not in contexts:
                continue
            a = m[metric]
            if idx is not None:
                a = a[np.ix_(idx[(ms, ws, c, "s")], idx[(mr, wr, c, "r")])]
            cells.setdefault((ms, mr), []).append(a.mean())
        for (ms, mr), vals in cells.items():
            by_class[pair_class(ms, mr)].append(float(np.mean(vals)))
        for k, v in by_class.items():
            res[(wtype, k)] = float(np.mean(v))
        res[(wtype, "diff_same_minus_cross")] = res[(wtype, "same_model")] - res[(wtype, "cross_developer")]
    return res


def per_model(M, idx, metric, wtype, context=None):
    out = {}
    for ms, mr in itertools.product(MODELS, MODELS):
        vals = []
        for (a, b, ws, wr, c), m in M.items():
            if (a, b) != (ms, mr) or (ws, wr) not in WPAIRS[wtype] or (context and c != context):
                continue
            x = m[metric]
            if idx is not None:
                x = x[np.ix_(idx[(a, ws, c, "s")], idx[(b, wr, c, "r")])]
            vals.append(x.mean())
        if vals:
            out[(ms, mr)] = float(np.mean(vals))
    return out


def bootstrap_indices(snd, rcv, rng):
    idx = {}
    for (m, w, c), lst in snd.items():
        idx[(m, w, c, "s")] = rng.integers(0, len(lst), len(lst))
    for (m, w, c), lst in rcv.items():
        idx[(m, w, c, "r")] = rng.integers(0, len(lst), len(lst))
    return idx


def ci(samples):
    a = np.array(samples)
    return [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--boot", type=int, default=10000)
    args = ap.parse_args()
    stage_dir = ROOT / args.stage
    snd, rcv, status = load(stage_dir)
    M = matrices(snd, rcv)
    rng = np.random.default_rng(20261109)
    report = {"stage": args.stage, "n_samples": {f"{k[0]}|{k[1]}|{k[2]}": len(v) for k, v in sorted(snd.items())},
              "status": {"|".join(k): v for k, v in sorted(status.items())}, "metrics": {}}
    groups = {"pooled": CONTEXTS, "C0": ("C0",), "C1": ("C1",)}
    for metric in ("full", "value", "lenient", "top3"):
        for gname, ctx in groups.items():
            point = estimates(M, None, metric, ctx)
            boots = collections.defaultdict(list)
            nb = args.boot if (metric == "full" and gname == "pooled") else max(1000, args.boot // 10)
            for _ in range(nb):
                e = estimates(M, bootstrap_indices(snd, rcv, rng), metric, ctx)
                for k, v in e.items():
                    boots[k].append(v)
            out = {}
            for k, v in point.items():
                b = np.array(boots[k])
                rec = {"estimate": v, "ci95": ci(b)}
                if k[1] == "diff_same_minus_cross":
                    rec["boot_frac_le_0"] = float((b <= 0).mean())
                out[f"{k[0]}|{k[1]}"] = rec
            report["metrics"][f"{metric}|{gname}"] = out
    for wtype in ("same", "cross"):
        for ctx in (None, "C0", "C1"):
            pm = per_model(M, None, "full", wtype, ctx)
            report.setdefault("matrix_full", {})[f"{wtype}|{ctx or 'pooled'}"] = {f"{a}->{b}": v for (a, b), v in pm.items()}
    # Descriptive: most common scheme shapes per model (digits replaced by N), senders only.
    shapes = collections.defaultdict(collections.Counter)
    for (m, w, c), lst in snd.items():
        for t in lst:
            if t:
                shapes[(m, c)][S.norm(t["7"]).replace("07", "NN").replace("7", "N")] += 1
    report["sender_shapes_value7"] = {f"{m}|{c}": cnt.most_common(4) for (m, c), cnt in sorted(shapes.items())}
    out_path = stage_dir / "analysis.json"
    out_path.write_text(json.dumps(report, indent=1))
    p = report["metrics"]["full|pooled"]
    for wt in ("same", "cross"):
        print(wt, {k.split("|")[1]: round(v["estimate"], 3) for k, v in p.items() if k.startswith(wt)},
              "diff CI", [round(x, 3) for x in p[f"{wt}|diff_same_minus_cross"]["ci95"]])
    print("wrote", out_path)


if __name__ == "__main__":
    main()
