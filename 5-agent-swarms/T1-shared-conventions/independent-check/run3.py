import sys, json, re, collections, itertools
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.path.insert(1, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True
import mine as M
import score as S
rows = M.load()
# tails of invalid outputs
for i in ("main-oss120b-receiver-W2-C1-006","main-oss120b-receiver-W2-C1-021","main-deepseek-receiver-W2-C1-014","main-deepseek-receiver-W2-C1-022"):
    r = next(r for r in rows if r["id"]==i)
    t = r["text"]
    print(i, "TAIL:", repr(t[-120:]))
    # can the primary be recovered as a JSON object?
    k = t.find('"primary"'); j = t.find("{", k)
    try:
        obj, end = M.DEC.raw_decode(t, j); print("  primary recoverable:", M.as_table(obj) is not None)
    except ValueError as e: print("  primary NOT recoverable", e)

# normalisation: compare mine vs score.norm on every string in every table
allp = collections.Counter()
for r in rows:
    obj = M.first_object(r["text"])
    if not obj: continue
    if r["role"]=="sender": tabs=[obj]
    else:
        tabs=[obj.get("primary")]+(obj.get("alternatives") if isinstance(obj.get("alternatives"),list) else [])[:2]
    for t in tabs:
        if isinstance(t, dict):
            for v in t.values():
                if isinstance(v,str): allp[v]+=1
print("distinct path strings:", len(allp))
dif=[(p,S.norm(p),M.norm(p)) for p in allp if S.norm(p)!=M.norm(p)]
print("norm disagreements:", len(dif), dif[:10])
# unusual strings
odd=[p for p in allp if re.search(r"://|%|#|\?|\s|[A-Z]|\.\.|//|files\.lab|^[^/]|\s$", p)]
print("odd strings:", len(odd))
cats=collections.defaultdict(list)
for p in odd:
    for name,pat in [("scheme",r"://"),("pct",r"%"),("frag",r"#"),("query",r"\?"),("space",r"\s"),("upper",r"[A-Z]"),("dotdot",r"\.\."),("dblslash",r"//"),("hostbare",r"files\.lab"),("noslash",r"^[^/]")]:
        if re.search(pat,p): cats[name].append(p)
for k,v in cats.items(): print(k,len(v),v[:6])
# strings where normalisation changes more than trivial: trailing slash counts
ts=[p for p in allp if p.endswith("/") and len(p)>1]
print("trailing-slash strings:",len(ts), ts[:5])
ns=[p for p in allp if p!=p.strip()]
print("whitespace-padded:",ns[:5])
