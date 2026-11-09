import sys, json, re, collections
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.path.insert(1, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True
import mine as M
import score as S   # only for task 2 comparison
rows = M.load()
print("== invalid outputs")
for r in rows:
    p = M.parse(r["role"], r["text"])
    ok = p is not None if r["role"]=="sender" else (p is not None and p["primary"] is not None)
    sp = S.parse_sender(r["text"]) if r["role"]=="sender" else S.parse_receiver(r["text"])[0]
    if not ok or sp is None:
        print("-----", r["id"], "stop:", r["stop_reason"], "usage:", r["usage"], "mine_ok", ok, "theirs_ok", sp is not None)
        print(repr(r["text"][:1500]), "... len", len(r["text"]))
        print("raw block types:", [list(c.keys()) for c in r["raw_content"]])
