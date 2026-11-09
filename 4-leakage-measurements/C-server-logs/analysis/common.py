"""Shared loading and server-side event classification for the detectors.

The detectors may use only the server view: time, user, ip, method, true path (router log), status,
response bytes. Columns starting with g_ are ground truth, used only to score.
"""
import json
import re
import numpy as np
import pandas as pd

CACHE_REPO = "github-remote-cache"


def _lines(path):
    import gzip, os
    if os.path.exists(path):
        return open(path)
    return gzip.open(path + ".gz", "rt")


def load(ds):
    rows = [json.loads(l) for l in _lines(f"{ds}/requests.jsonl")]
    df = pd.DataFrame(rows)
    df = df.rename(columns={"s_t": "t", "s_user": "user", "s_ip": "ip", "s_router_method": "method",
                            "s_path": "rpath", "s_router_status": "status", "s_resp_bytes": "resp"})
    df["t0"] = df["t"].min()
    df["rel"] = df["t"] - df["t0"]
    df = df.sort_values("t").reset_index(drop=True)
    msgs = [json.loads(l) for l in _lines(f"{ds}/messages.jsonl")]
    return df, pd.DataFrame(msgs)


def split_path(p):
    """Return (repo, dirpath-or-file path, query). Handles /api/storage/<repo>/<path> too."""
    path, _, query = p.partition("?")
    path = path.split(";")[0] if "/api/" not in path else path   # matrix params are not part of the path
    segs = [s for s in path.split("/") if s]
    if segs and segs[0] == "artifactory":
        segs = segs[1:]
    api = False
    if len(segs) >= 2 and segs[0] == "api" and segs[1] == "storage":
        segs = segs[2:]
        api = True
    if segs and segs[0] == "api":
        return None, "", query, True
    if not segs:
        return None, "", query, api
    return segs[0], "/".join(segs[1:]), query, api


def classify(df):
    """Add server-side event type and target path. Uses only server-visible fields."""
    repo, tgt, api, trail = [], [], [], []
    for p in df["rpath"]:
        r, t, q, a = split_path(p)
        repo.append(r); tgt.append(t); api.append(a)
        trail.append(p.partition("?")[0].endswith("/"))
    df["repo"], df["tgt"], df["api"], df["trail"] = repo, tgt, api, trail
    # directories we know exist: parents of anything written or fetched, plus repo roots
    dirs = set()
    mk = (df["method"] == "MKCOL") & (df["status"] == 201)
    fetch = (df["method"].isin(["GET", "PUT", "HEAD"])) & ~df["api"] & ~df["trail"]
    for r, t in zip(df.loc[mk | fetch, "repo"], df.loc[mk | fetch, "tgt"]):
        if r is None:
            continue
        segs = t.split("/")
        for i in range(0, len(segs)):
            dirs.add((r, "/".join(segs[:i])))
    for r, t in zip(df.loc[mk, "repo"], df.loc[mk, "tgt"]):
        dirs.add((r, t))
    ev = []
    for m, r, t, a, tr in zip(df["method"], df["repo"], df["tgt"], df["api"], df["trail"]):
        if r is None:
            ev.append("OTHER")
        elif m == "MKCOL":
            ev.append("MKCOL_CACHE" if r.endswith("-cache") else "MKCOL_OTHER")
        elif m == "PROPFIND":
            ev.append("LIST")
        elif m in ("GET", "HEAD") and a and (r, t) in dirs:
            ev.append("LIST")
        elif m == "GET" and tr and not a:
            ev.append("LIST")
        elif m == "PUT":
            ev.append("PUT")
        elif m in ("GET", "HEAD"):
            ev.append("FETCH")
        else:
            ev.append("OTHER")
    df["ev"] = ev
    df["dir"] = [f"{r}/{t}".rstrip("/") if r else "" for r, t in zip(df["repo"], df["tgt"])]
    return df


def pr(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    return p, r
