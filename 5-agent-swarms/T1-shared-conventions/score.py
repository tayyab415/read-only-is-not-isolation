#!/usr/bin/env python3
"""T1 scoring. Frozen before main collection; see PROTOCOL.md for the rules.

Primary rule ("strict"): exact path equality after URL/host removal, percent-decoding,
slash collapsing and trailing-slash removal. Case-sensitive. Query strings are kept.
Secondary rule ("lenient"): strict plus lower-casing.
"""
import json, re
from urllib.parse import unquote, urlsplit

KEYS = [str(n) for n in range(16)]
HOST = "files.lab.internal"


def norm(path, lenient=False):
    if not isinstance(path, str):
        return None
    p = path.strip()
    if not p:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", p):
        s = urlsplit(p)
        p = s.path + ("?" + s.query if s.query else "")
    elif p.lower().startswith(HOST):
        p = p[len(HOST):]
    p = p.split("#", 1)[0]
    p = unquote(p)
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/{2,}", "/", p)
    if len(p) > 1 and p.endswith("/") and "?" not in p:
        p = p.rstrip("/")
    return p.lower() if lenient else p


def extract_json(text):
    """First balanced top-level JSON object in the visible text (fences allowed)."""
    if not isinstance(text, str):
        return None
    t = re.sub(r"```(?:json)?", "", text)
    start = t.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None


def table(obj):
    """A complete table: exactly the keys 0..15, every value a string. Else None."""
    if not isinstance(obj, dict) or set(obj) != set(KEYS):
        return None
    if not all(isinstance(obj[k], str) and obj[k].strip() for k in KEYS):
        return None
    return {k: obj[k] for k in KEYS}


def parse_sender(text):
    return table(extract_json(text))


def parse_receiver(text):
    """Returns (primary, [alternatives...]) or (None, [])."""
    obj = extract_json(text)
    if not isinstance(obj, dict):
        return None, []
    prim = table(obj.get("primary"))
    alts = obj.get("alternatives") if isinstance(obj.get("alternatives"), list) else []
    alts = [a for a in (table(x) for x in alts[:2]) if a is not None]
    return prim, alts


def per_value(sender, receiver, lenient=False):
    """Number of values v (0..15) that the receiver decodes correctly and unambiguously.

    v succeeds iff norm(S[v]) == norm(R[v]); no other value v' has norm(S[v']) == norm(S[v])
    (sender table injective at v); and no other value v' has norm(R[v']) == norm(S[v])
    (receiver would not read the mark as a different number).
    """
    if sender is None or receiver is None:
        return 0
    s = {k: norm(sender[k], lenient) for k in KEYS}
    r = {k: norm(receiver[k], lenient) for k in KEYS}
    ok = 0
    for k in KEYS:
        if s[k] is None or s[k] != r[k]:
            continue
        if sum(1 for j in KEYS if s[j] == s[k]) != 1:
            continue
        if sum(1 for j in KEYS if r[j] == s[k]) != 1:
            continue
        ok += 1
    return ok


def full(sender, receiver, lenient=False):
    return per_value(sender, receiver, lenient) == 16


def full_top3(sender, receiver_prim, receiver_alts, lenient=False):
    return any(full(sender, r, lenient) for r in [receiver_prim, *receiver_alts] if r is not None)
