import sys, json, random, collections, itertools, hashlib
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.path.insert(1, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True
import mine as M
import score as S
T=str(__import__("pathlib").Path(__file__).resolve().parents[1]) + "/"
# freeze hashes
fz=json.load(open(T+"FREEZE.json"))["sha256"]
for n,h in fz.items(): print(n, hashlib.sha256(open(T+n,"rb").read()).hexdigest()==h)
rows=M.load()
snd=collections.defaultdict(dict); rcv=collections.defaultdict(dict)
for r in rows:
    p=M.parse(r["role"],r["text"])
    (snd if r["role"]=="sender" else rcv)[(r["model"],r["wording"],r["context"])][r["idx"]]=(r["id"],p if r["role"]=="sender" else (p and p["primary"]), r["text"])
rng=random.Random(20261109)
def allpairs(pred):
    out=[]
    for ms,mr in itertools.product(M.MODELS,M.MODELS):
        if not pred(ms,mr): continue
        for c in ("C0","C1"):
            for ws in ("W1","W2"):
                for wr in ("W1","W2"):
                    for i in range(24):
                        for j in range(24):
                            out.append((ms,mr,ws,wr,c,i,j))
    return out
def verdict_mine(a,b):
    return M.decoded_values(a,b)==16
def verdict_theirs(a,b): return S.full(a,b)
cross=[p for p in allpairs(lambda a,b:M.pair_class(a,b)=="cross_developer") if verdict_mine(snd[(p[0],p[2],p[4])][p[5]][1], rcv[(p[1],p[3],p[4])][p[6]][1])]
same=[p for p in allpairs(lambda a,b:a==b) if not verdict_mine(snd[(p[0],p[2],p[4])][p[5]][1], rcv[(p[1],p[3],p[4])][p[6]][1])]
print("eligible compat cross-dev pairs:",len(cross),"incompat same-model pairs:",len(same))
def short(t):
    return None if t is None else [t[str(k)] for k in range(16)]
lines=[]
def dump(tag,pairs):
    for p in rng.sample(pairs,10):
        ms,mr,ws,wr,c,i,j=p
        sid,s,_=snd[(ms,ws,c)][i]; rid,r,_=rcv[(mr,wr,c)][j]
        mine=verdict_mine(s,r); th=verdict_theirs(s,r)
        pv=M.decoded_values(s,r)
        lines.append(f"### {tag}: S={sid}  R={rid}   mine_full={mine} theirs_full={th} decoded_values={pv}")
        lines.append("sender  : "+json.dumps(short(s)))
        lines.append("receiver: "+json.dumps(short(r)))
        if s and r:
            ns=[M.norm(s[str(k)]) for k in range(16)]; nr=[M.norm(r[str(k)]) for k in range(16)]
            diff=[k for k in range(16) if ns[k]!=nr[k]]
            lines.append(f"  norm-equal at all 16: {not diff}; mismatching values: {diff}; sender distinct: {len(set(ns))}; receiver distinct: {len(set(nr))}")
dump("COMPATIBLE cross-developer",cross); dump("INCOMPATIBLE same-model",same)
open("spotcheck.txt","w").write("\n".join(lines))
