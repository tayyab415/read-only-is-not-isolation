import sys, json, collections, itertools
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import mine as M
rows=M.load()
A=json.load(open(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "main" / "analysis.json")))
print("status:",{k:v for k,v in A["status"].items() if not k.endswith("|valid")})
print("n_samples set:",set(A["n_samples"].values()), len(A["n_samples"]))
snd=collections.defaultdict(list); rcv=collections.defaultdict(list)
for r in sorted(rows,key=lambda r:r["id"]):
    p=M.parse(r["role"],r["text"])
    (snd if r["role"]=="sender" else rcv)[(r["model"],r["wording"],r["context"])].append(p if r["role"]=="sender" else (p and p["primary"]))
def rate(ms,mr,wps,ctxs):
    v=[]
    for c in ctxs:
        for ws,wr in wps:
            S,R=snd[(ms,ws,c)],rcv[(mr,wr,c)]
            v.append(sum(M.decoded_values(s,r)==16 for s in S for r in R)/(len(S)*len(R)))
    return sum(v)/len(v)
W={"same":[("W1","W1"),("W2","W2")],"cross":[("W1","W2"),("W2","W1")]}
mx=0;n=0
for key,d in A["matrix_full"].items():
    w,c=key.split("|"); ctxs=("C0","C1") if c=="pooled" else (c,)
    for pair,val in d.items():
        a,b=pair.split("->"); x=rate(a,b,W[w],ctxs); mx=max(mx,abs(x-val)); n+=1
print("matrix cells compared",n,"max abs diff",mx)
# per-model invalid counts mine
inv=collections.Counter()
for k in rcv:
    for p in rcv[k]:
        if p is None: inv[(k[0],"receiver")]+=1
for k in snd:
    for p in snd[k]:
        if p is None: inv[(k[0],"sender")]+=1
print("mine invalid:",dict(inv))
# where's the fragment string / sender-vs-receiver; rows where a path normalisation changed anything beyond slash/strip
for r in rows:
    if "#fragment" in r["text"]: print("fragment in",r["id"])
# top3 with primary invalid: any receiver whose primary invalid but a valid alternative
