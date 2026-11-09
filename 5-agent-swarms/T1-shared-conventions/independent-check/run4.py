import sys, json, re, collections, itertools, posixpath
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
import mine as M
rows = M.load()
# prose around JSON / think tags
pre=collections.Counter(); post=collections.Counter(); think=0; ex=[]
for r in rows:
    t=r["text"]
    if re.search(r"<think|</think|<reasoning", t): think+=1
    i=t.find("{")
    if i<0: pre["nobrace"]+=1; continue
    head=re.sub(r"```(json)?","",t[:i]).strip()
    if head: pre[r["model"]]+=1; ex.append((r["id"],head[:200]))
    try:
        o,e=M.DEC.raw_decode(t,i)
        tail=re.sub(r"```","",t[e:]).strip()
        if tail: post[r["model"]]+=1; ex.append((r["id"],"TAIL:"+tail[:200]))
    except ValueError: pass
print("think tags",think,"pre-prose",dict(pre),"post-prose",dict(post)); print(ex[:12])
print("stop reasons", collections.Counter((r["model"],r["stop_reason"]) for r in rows if r["stop_reason"]!="end_turn"))
# table-level oddities among parsed
inj=collections.Counter(); shape=collections.Counter(); extra=0
for r in rows:
    o=M.first_object(r["text"])
    if not isinstance(o,dict): continue
    if r["role"]=="receiver":
        if "primary" not in o: shape["no primary"]+=1
        a=o.get("alternatives"); 
        if not isinstance(a,list): shape["alts not list"]+=1
        elif len(a)>2: shape["alts>2"]+=1
        else:
            shape[f"nalts={len(a)}"]+=1
            for x in a:
                if M.as_table(x) is None: shape["alt invalid table"]+=1
        t=o.get("primary")
    else: t=o
    if isinstance(t,dict):
        if set(t)!=set(M.KEYS): shape["key-set mismatch "+r["role"]]+=1
        elif M.as_table(t) is None: shape["bad values"]+=1
        else:
            n=[M.norm(t[k]) for k in M.KEYS]
            if len(set(n))<16: inj[(r["model"],r["role"])]+=1
print(shape); print("non-injective tables (after norm):",dict(inj))
