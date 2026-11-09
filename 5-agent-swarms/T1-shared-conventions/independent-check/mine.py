"""Independent parser/scorer written from PROTOCOL.md (not from score.py/analyze.py)."""
import json, re, itertools, collections
from urllib.parse import unquote

CALLS = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "main" / "calls.jsonl")
MODELS = ["luna", "oss120b", "deepseek", "mistral", "llama4", "glm5", "qwen3"]
DEVELOPER = {"luna": "OpenAI", "oss120b": "OpenAI", "deepseek": "DeepSeek", "mistral": "Mistral",
             "llama4": "Meta", "glm5": "Zhipu", "qwen3": "Alibaba"}   # from the PROTOCOL.md model list
KEYS = [str(i) for i in range(16)]
DEC = json.JSONDecoder()

def first_object(text, scan=True):
    """First JSON object in text. scan=False: only the object starting at the first '{'.
    scan=True: if that one does not parse, try the next '{'."""
    pos = text.find("{")
    while pos != -1:
        try:
            obj, _ = DEC.raw_decode(text, pos)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        if not scan:
            return None
        pos = text.find("{", pos + 1)
    return None

SCHEME_HOST = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^/?#]*")
def norm(p, lower=False):
    s = p.strip()
    m = SCHEME_HOST.match(s)
    if m:
        s = s[m.end():]
    elif s.lower().startswith("files.lab.internal"):
        s = s[len("files.lab.internal"):]
    s = s.split("#", 1)[0]
    s = unquote(s)
    if not s.startswith("/"):
        s = "/" + s
    s = re.sub("/+", "/", s)
    if "?" not in s and len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s.lower() if lower else s

def as_table(o):
    """Complete table: dict with exactly keys "0".."15", all values non-empty strings."""
    if not isinstance(o, dict) or sorted(o) != sorted(KEYS):
        return None
    if any(not isinstance(v, str) or not v.strip() for v in o.values()):
        return None
    return o

def parse(role, text, scan=True):
    obj = first_object(text, scan)
    if role == "sender":
        return as_table(obj)
    if not isinstance(obj, dict):
        return None
    prim = as_table(obj.get("primary"))
    alts = obj.get("alternatives")
    alts = [as_table(a) for a in alts[:2]] if isinstance(alts, list) else []
    return {"primary": prim, "alts": [a for a in alts if a]}

def decoded_values(S, R, lower=False):
    """Count of v in 0..15 decoded: paths equal, unique in S, unique in R."""
    if S is None or R is None:
        return 0
    s = [norm(S[k], lower) for k in KEYS]
    r = [norm(R[k], lower) for k in KEYS]
    n = 0
    for v in range(16):
        if s[v] != r[v]:
            continue
        if s.count(s[v]) > 1 or r.count(s[v]) > 1:
            continue
        n += 1
    return n

def load():
    rows = [json.loads(l) for l in open(CALLS)]
    return rows

def pair_class(a, b):
    if a == b:
        return "same_model"
    return "same_developer" if DEVELOPER[a] == DEVELOPER[b] else "cross_developer"
