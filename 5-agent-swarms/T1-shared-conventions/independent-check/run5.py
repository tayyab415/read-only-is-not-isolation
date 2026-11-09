import sys, json, re, collections, itertools, posixpath
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import numpy as np
import mine as M
rows = M.load()
W = {"same": [("W1","W1"),("W2","W2")], "cross": [("W1","W2"),("W2","W1")]}

def rescue_primary(text):
    k = text.find('"primary"'); j = text.find("{", k)
    try:
        o, _ = M.DEC.raw_decode(text, j); return M.as_table(o)
    except ValueError: return None

def build(normf, rescue=False):
    snd, rcv = collections.defaultdict(list), collections.defaultdict(list)
    for r in sorted(rows, key=lambda r: r["id"]):
        key=(r["model"], r["wording"], r["context"]); p = M.parse(r["role"], r["text"])
        if r["role"]=="sender": snd[key].append(None if p is None else tuple(normf(p[k]) for k in M.KEYS))
        else:
            prim = None if p is None else p["primary"]
            if prim is None and rescue: prim = rescue_primary(r["text"])
            rcv[key].append(None if prim is None else tuple(normf(prim[k]) for k in M.KEYS))
    return snd, rcv

def full_mat(S, R):
    # full compat: tuples equal and all 16 distinct (equivalent to the per-value rule)
    m = np.zeros((len(S), len(R)))
    for i, s in enumerate(S):
        for j, r in enumerate(R):
            m[i,j] = s is not None and r is not None and s == r and len(set(s)) == 16
    return m

def cells(snd, rcv):
    return {(ms,mr,ws,wr,c): full_mat(snd[(ms,ws,c)], rcv[(mr,wr,c)])
            for ms in M.MODELS for mr in M.MODELS for ws in ("W1","W2") for wr in ("W1","W2") for c in ("C0","C1")}

def estimate(C, idx=None, ctxs=("C0","C1")):
    out = {}
    for w, wps in W.items():
        cls = collections.defaultdict(list)
        for ms, mr in itertools.product(M.MODELS, M.MODELS):
            v = []
            for c in ctxs:
                for ws, wr in wps:
                    m = C[(ms,mr,ws,wr,c)]
                    if idx is not None: m = m[np.ix_(idx[(ms,ws,c,"s")], idx[(mr,wr,c,"r")])]
                    v.append(m.mean())
            cls[M.pair_class(ms,mr)].append(np.mean(v))
        for k, x in cls.items(): out[(w,k)] = float(np.mean(x))
        out[(w,"diff")] = out[(w,"same_model")] - out[(w,"cross_developer")]
    return out

def show(label, C):
    e = estimate(C)
    print(f"{label:<34}", " ".join(f"{w}:{k[:4]}={e[(w,k)]:.4f}" for w in W for k in ("same_model","same_developer","cross_developer","diff")))
    return e

variants = {
 "baseline (my norm)": (M.norm, False),
 "rescue 4 malformed primaries": (M.norm, True),
 "dot-segment resolution": (lambda p: (lambda n: (posixpath.normpath(n.split('?')[0]) + (('?'+n.split('?',1)[1]) if '?' in n else '')) if n.split('?')[0]!='/' else n)(M.norm(p)), False),
 "lenient (lowercase)": (lambda p: M.norm(p, True), False),
}
base=None
for name,(f,res) in variants.items():
    C = cells(*build(f,res)); e = show(name, C)
    if base is None: base=(C,e)

# my own bootstrap: resample sender / receiver samples within each (model, wording, context)
C = base[0]
rng = np.random.default_rng(777)
B = 2000
bs = collections.defaultdict(list)
for _ in range(B):
    idx = {}
    for m in M.MODELS:
        for w in ("W1","W2"):
            for c in ("C0","C1"):
                idx[(m,w,c,"s")] = rng.integers(0,24,24); idx[(m,w,c,"r")] = rng.integers(0,24,24)
    e = estimate(C, idx)
    for k,v in e.items(): bs[k].append(v)
a = json.load(open(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "main" / "analysis.json")))["metrics"]["full|pooled"]
for w in W:
    for k,name in (("diff","diff_same_minus_cross"),("same_model","same_model"),("cross_developer","cross_developer")):
        lo,hi = np.percentile(bs[(w,k)],[2.5,97.5]); t=a[f"{w}|{name}"]["ci95"]
        print(f"boot {w}|{k:<16} mine [{lo:.4f},{hi:.4f}]  theirs [{t[0]:.4f},{t[1]:.4f}]  frac<=0 mine {np.mean(np.array(bs[(w,k)])<=0):.4f}", a[f"{w}|{name}"].get("boot_frac_le_0",""))
