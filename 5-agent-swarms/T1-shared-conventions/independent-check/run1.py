import sys, json, itertools, collections
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import mine as M
rows = M.load()
ids = [r["id"] for r in rows]
print("rows", len(rows), "unique ids", len(set(ids)))
cell = collections.Counter((r["model"], r["role"], r["wording"], r["context"]) for r in rows)
print("cells", len(cell), "sizes", set(cell.values()), "events", collections.Counter(r["event"] for r in rows), "kinds", collections.Counter(r["kind"] for r in rows))
for scan in (True, False):
    snd, rcv = collections.defaultdict(list), collections.defaultdict(list)
    bad = []
    for r in sorted(rows, key=lambda r: r["id"]):
        p = M.parse(r["role"], r["text"], scan)
        key = (r["model"], r["wording"], r["context"])
        if r["role"] == "sender":
            snd[key].append(p); ok = p is not None
        else:
            rcv[key].append(p); ok = p is not None and p["primary"] is not None
        if not ok: bad.append(r["id"])
    print("scan" if scan else "firstbrace", "invalid:", len(bad))
    # point estimates
    def est(ctxs, wps):
        cls = collections.defaultdict(list)
        for ms, mr in itertools.product(M.MODELS, M.MODELS):
            vals = []
            for c in ctxs:
                for ws, wr in wps:
                    S, R = snd[(ms, ws, c)], rcv[(mr, wr, c)]
                    tot = 0
                    for s in S:
                        for rr in R:
                            tot += (rr is not None and rr["primary"] is not None and s is not None
                                    and M.decoded_values(s, rr["primary"]) == 16)
                    vals.append(tot / (len(S) * len(R)))
            cls[M.pair_class(ms, mr)].append(sum(vals) / len(vals))
        out = {k: sum(v) / len(v) for k, v in cls.items()}
        out["n"] = {k: len(v) for k, v in cls.items()}
        out["diff"] = out["same_model"] - out["cross_developer"]
        return out
    W = {"same": [("W1", "W1"), ("W2", "W2")], "cross": [("W1", "W2"), ("W2", "W1")]}
    res = {}
    for g, ctxs in (("pooled", ("C0", "C1")), ("C0", ("C0",)), ("C1", ("C1",))):
        for w, wps in W.items():
            res[f"{w}|{g}"] = est(ctxs, wps)
    if scan:
        json.dump(res, open(str(__import__("pathlib").Path(__file__).resolve().parent / "mine_estimates.json"), "w"), indent=1)
        a = json.load(open(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "main" / "analysis.json")))
        print(f"{'cell':<14}{'class':<18}{'mine':>9}{'theirs':>9}{'|d|':>9}")
        mx = 0
        for k, v in res.items():
            w, g = k.split("|")
            for cl in ("same_model", "same_developer", "cross_developer", "diff"):
                tk = f"{w}|{cl if cl!='diff' else 'diff_same_minus_cross'}"
                t = a["metrics"][f"full|{g}"][tk]["estimate"]
                mx = max(mx, abs(t - v[cl]))
                print(f"{k:<14}{cl:<18}{v[cl]:9.4f}{t:9.4f}{abs(t-v[cl]):9.5f}")
        print("max abs diff", mx, "classes n", res["same|pooled"]["n"])
