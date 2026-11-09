#!/usr/bin/env python3
"""Detectors and scoring for workstream C.  Usage: detect.py <dataset-dir> <phase-split-seconds> <out.json>

Every detector reads only server-visible columns (t, user, ip, method, rpath, status, resp). Labels
(g_*) are used to score.
"""
import json
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import classify, load, pr

ds, SPLIT, outp = sys.argv[1], float(sys.argv[2]), sys.argv[3]
df, msgs = load(ds)
df = classify(df)
df["board"] = df["g_board_use"].astype(bool)
df["phase"] = np.where(df["rel"] < SPLIT, "A", "B")
R = {}   # results


def rnd(x, n=3):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


R["dataset"] = dict(requests=len(df), clients=sorted(df["user"].unique().tolist()),
                    duration_min=rnd(df["rel"].max() / 60, 1),
                    board_requests=int(df["board"].sum()),
                    by_event=df["ev"].value_counts().to_dict())

# ------------------------------------------------------------------ D1: MKCOL counts
mk = df[df["method"] == "MKCOL"]
mk_ok = mk[mk["status"] == 201]
d1 = {}
for name, sel in {"any MKCOL": mk_ok, "MKCOL into a *-cache repo": mk_ok[mk_ok["ev"] == "MKCOL_CACHE"]}.items():
    tp = int(sel["board"].sum()); fp = int((~sel["board"]).sum())
    fn = int(mk_ok["board"].sum()) - tp
    p, r = pr(tp, fp, fn)
    d1[name] = dict(flagged=len(sel), tp=tp, fp=fp, fn=fn, precision=rnd(p), recall=rnd(r))
hours = df["rel"].max() / 3600
per_client = []
for u, g in df.groupby("user"):
    m = g[(g["ev"] == "MKCOL_CACHE") & (g["status"] == 201)]
    m_any = g[(g["method"] == "MKCOL") & (g["status"] == 201)]
    ts = m["rel"].sort_values().tolist()
    per_client.append(dict(client=u, mkcol_cache=len(m), mkcol_any=len(m_any), per_hour=rnd(len(m) / hours, 1),
                           first_min=rnd(ts[0] / 60, 1) if ts else None,
                           third_min=rnd(ts[2] / 60, 1) if len(ts) > 2 else None,
                           tenth_min=rnd(ts[9] / 60, 1) if len(ts) > 9 else None,
                           truth_board=bool(g["board"].any())))
d1["per_client"] = per_client
R["D1_mkcol"] = d1

# ------------------------------------------------------------------ D2: repeated listing of one path
lst = df[df["ev"] == "LIST"].copy()


def trailing_counts(g, W):
    t = g["rel"].to_numpy()
    lo = np.searchsorted(t, t - W, side="left")
    return np.arange(len(t)) - lo + 1


def d2_eval(frame, W, N):
    flag = pd.Series(False, index=frame.index)
    for _, g in frame.groupby(["user", "dir"]):
        g = g.sort_values("rel")
        flag.loc[g.index] = trailing_counts(g, W) >= N
    return flag


d2 = {"W_seconds": 600, "rows": []}
by_role = {}
for scope, frame in {"all dirs": lst, "cache repos only": lst[lst["repo"].fillna("").str.endswith("-cache")]}.items():
    for N in (3, 5, 10, 20):
        f = d2_eval(frame, 600, N)
        tp = int((f & frame["board"]).sum()); fp = int((f & ~frame["board"]).sum())
        fn = int(lst["board"].sum()) - tp   # recall is against all board listings, even those outside the scope
        p, r = pr(tp, fp, fn)
        d2["rows"].append(dict(scope=scope, N=N, flagged=int(f.sum()), tp=tp, fp=fp, fn=fn, precision=rnd(p), recall=rnd(r)))
        if scope == "cache repos only" and N == 5:
            for (u, ph), g in frame.assign(f=f).groupby(["user", "phase"]):
                by_role[f"{u}/{ph}"] = dict(listings=len(g), board=int(g["board"].sum()), flagged=int(g["f"].sum()))
# the one-line rule: any listing under a cache repo
cf = lst[lst["repo"].fillna("").str.endswith("-cache")]
tp = int(cf["board"].sum()); fp = int((~cf["board"]).sum()); fn = int(lst["board"].sum()) - tp
p, r = pr(tp, fp, fn)
d2["any_cache_listing"] = dict(flagged=len(cf), tp=tp, fp=fp, fn=fn, precision=rnd(p), recall=rnd(r))
d2["by_client_phase_N5_cache"] = by_role
R["D2_repeat_listing"] = d2

# ------------------------------------------------------------------ D3: periodicity
series = []
req_like = df[df["ev"].isin(["LIST", "FETCH"])]
for (u, ph, d, ev), g in req_like.groupby(["user", "phase", "dir", "ev"]):
    # fetch series are keyed by the exact file path, which we keep in rpath
    pass
req_like = req_like.assign(key=np.where(req_like["ev"] == "FETCH", req_like["rpath"], req_like["dir"]))
for (u, ph, k, ev), g in req_like.groupby(["user", "phase", "key", "ev"]):
    if len(g) < 8:
        continue
    t = np.sort(g["rel"].to_numpy())
    dt = np.diff(t)
    cv = dt.std() / dt.mean()
    # dominant period: mode of rounded gaps
    med = float(np.median(dt))
    series.append(dict(user=u, phase=ph, key=k, ev=ev, n=len(g), median_gap=rnd(med, 1), cv=rnd(cv),
                       board_frac=rnd(g["board"].mean()), cache=str(g["repo"].iloc[0]).endswith("-cache")))
sdf = pd.DataFrame(series)
d3 = {"series": series}
if len(sdf):
    sdf["truth"] = sdf["board_frac"] > 0.5
    rows = []
    for thr in (0.2, 0.35, 0.5, 0.7):
        for scope, sel in {"any series": sdf, "cache listings only": sdf[(sdf["ev"] == "LIST") & sdf["cache"]]}.items():
            f = sel[sel["cv"] < thr]
            tp = int(f["truth"].sum()); fp = int((~f["truth"]).sum()); fn = int(sdf["truth"].sum()) - tp
            p, r = pr(tp, fp, fn)
            rows.append(dict(cv_below=thr, scope=scope, flagged=len(f), tp=tp, fp=fp, fn=fn, precision=rnd(p), recall=rnd(r)))
    d3["rows"] = rows
R["D3_periodicity"] = d3

# ------------------------------------------------------------------ D4: write-then-read chains
gt_msgs = df[df["g_msg_kind"].isin(["ask", "answer", "inbox", "file_chunk"])].copy()
gt_msgs["to"] = gt_msgs["g_msg_to"]
directed = gt_msgs[(gt_msgs["to"] != "-") & (gt_msgs["to"] != gt_msgs["user"])]
# one message per id for file chunks (count once per file id)
first_chunk = directed[directed["g_msg_kind"] == "file_chunk"].drop_duplicates("g_msg_id")
directed1 = pd.concat([directed[directed["g_msg_kind"] != "file_chunk"], first_chunk]).sort_values("t")
true_pairs = Counter(zip(directed1["user"], directed1["to"]))
R["D4_truth"] = dict(directed_messages=len(directed1), by_kind=directed1["g_msg_kind"].value_counts().to_dict(),
                     distinct_pairs=len(true_pairs))

users = sorted(df["user"].unique())
lists_by_user = {u: g[["t", "dir"]].to_numpy() for u, g in lst.groupby("user")}
posts = df[(df["ev"] == "MKCOL_CACHE") & (df["status"] == 201)].copy()
posts["parent"] = ["/".join(d.split("/")[:-1]) for d in posts["dir"]]
root = "github-remote-cache"

# 4a exposure windows: the first listing of the parent dir by a given user after each post
exp_rows = []
dir_post = posts[posts["g_msg_kind"].isin(["ask", "answer", "inbox", "file_done", "broadcast"])]
lt = {(u, d): g["t"].to_numpy() for (u, d), g in lst.groupby(["user", "dir"])}
for _, p_ in dir_post.iterrows():
    recv = p_["g_msg_to"]
    for u in users:
        arr = lt.get((u, p_["parent"]))
        if u == p_["user"] or arr is None:
            continue
        i = np.searchsorted(arr, p_["t"])
        if i < len(arr):
            exp_rows.append(dict(kind=p_["g_msg_kind"], sender=p_["user"], reader=u, is_receiver=(u == recv), gap=arr[i] - p_["t"], phase=p_["phase"]))
ex = pd.DataFrame(exp_rows)
d4a = {}
if len(ex):
    for kind in ("ask", "answer", "inbox", "broadcast"):
        e = ex[ex["kind"] == kind]
        if kind == "broadcast":
            continue
        r_ = e[e["is_receiver"]]
        d4a[kind] = {f"recv_first_list_within_{W}s": rnd((r_["gap"] <= W).sum() / max(1, len(directed1[directed1['g_msg_kind'] == kind])), 3)
                     for W in (10, 30, 60, 120, 300)}
        d4a[kind]["receiver_median_gap_s"] = rnd(r_["gap"].median(), 1)
        # candidate set: readers whose first listing falls in the window; how many are there
        for W in (10, 30, 60):
            cand = e[e["gap"] <= W].groupby(["sender", "reader"]).size()  # placeholder; per-message candidate count below
        d4a[kind]["n_messages"] = int(len(directed1[directed1["g_msg_kind"] == kind]))
if len(ex):
    d4a["by_phase_recv_within_60s"] = {}
    for kind in ("ask", "answer", "inbox"):
        for ph in ("A", "B"):
            e = ex[(ex["kind"] == kind) & (ex["phase"] == ph) & ex["is_receiver"]]
            n = int(((directed1["g_msg_kind"] == kind) & (directed1["phase"] == ph)).sum())
            d4a["by_phase_recv_within_60s"][f"{kind}/{ph}"] = dict(n=n, within60=rnd((e["gap"] <= 60).sum() / n) if n else None,
                                                                    median_gap=rnd(e["gap"].median(), 1) if len(e) else None)
R["D4a_exposure"] = d4a

# 4b L0: reaction links from timing only (no paths, no names).
# For each post by B, find B's latest listing before it (<= REACT s). Senders A whose posts were first exposed by that
# listing (made after B's previous listing) are the candidates for "B answered A".
REACT = 30
answers = posts[posts["g_msg_kind"] == "answer"]
all_posts_t = {u: g["t"].to_numpy() for u, g in posts.groupby("user")}
l0_hits, l0_sizes, l0_top1, l0_ph = [], [], [], []
for _, a in answers.iterrows():
    B = a["user"]
    l0_ph.append(a["phase"])
    arr = np.array([t for (t, d) in lists_by_user.get(B, []) if d == root])
    i = np.searchsorted(arr, a["t"]) - 1
    if i < 0 or a["t"] - arr[i] > REACT:
        l0_hits.append(False); l0_sizes.append(0); continue
    lo = arr[i - 1] if i >= 1 else arr[i] - 3600
    cands = {}
    for A, ts in all_posts_t.items():
        if A == B:
            continue
        sel = ts[(ts > lo) & (ts <= arr[i])]
        if len(sel):
            cands[A] = sel.max()
    truth = a["g_msg_to"]
    l0_hits.append(truth in cands)
    l0_sizes.append(len(cands))
    if cands:
        # choose most recent poster: weak heuristic
        l0_top1.append(max(cands, key=cands.get) == truth)
    else:
        l0_top1.append(False)
sz = np.array(l0_sizes)
R["D4b_L0_timing_only"] = dict(
    answers=len(answers), react_window_s=REACT,
    sender_in_candidate_set=rnd(np.mean(l0_hits)) if l0_hits else None,
    median_candidates=rnd(np.median(sz[sz > 0]), 1) if (sz > 0).any() else None,
    mean_candidates_when_nonempty=rnd(sz[sz > 0].mean(), 2) if (sz > 0).any() else None,
    top1_most_recent_poster=rnd(np.mean(l0_top1)) if l0_top1 else None,
    top1_random_among_candidates=rnd(np.mean([1 / s if h else 0 for h, s in zip(l0_hits, l0_sizes) if s > 0]) if any(s > 0 for s in l0_sizes) else None),
    baseline_random_peer=rnd(1 / 5),
    by_phase={ph: dict(answers=int(sum(1 for x in l0_ph if x == ph)),
                       sender_in_candidates=rnd(np.mean([h for h, x in zip(l0_hits, l0_ph) if x == ph])) if any(x == ph for x in l0_ph) else None,
                       mean_candidates=rnd(np.mean([s_ for s_, x in zip(l0_sizes, l0_ph) if x == ph and s_ > 0]), 2) if any(x == ph and s_ > 0 for s_, x in zip(l0_sizes, l0_ph)) else None)
              for ph in ("A", "B")})

# pair-score version of L0: count of (A post -> B list -> B post within REACT) chains, compared with time-shifted null
def chain_scores(posts_t, lists_root, shift=None):
    sc = Counter()
    for B in users:
        arr = lists_root.get(B)
        pb = posts_t.get(B)
        if arr is None or pb is None or len(arr) == 0:
            continue
        for tb in pb:
            i = np.searchsorted(arr, tb) - 1
            if i < 0 or tb - arr[i] > REACT:
                continue
            lo = arr[i - 1] if i >= 1 else arr[i] - 3600
            for A, ts in posts_t.items():
                if A == B:
                    continue
                tsA = ts if shift is None else ((ts - shift) % (df["rel"].max() + 1))
                if ((tsA > lo) & (tsA <= arr[i])).any():
                    sc[(A, B)] += 1
    return sc


lists_root = {u: np.sort(g["t"].to_numpy()) for u, g in lst[lst["dir"] == root].groupby("user")}
pt = {u: np.sort(g["t"].to_numpy()) for u, g in posts.groupby("user")}
base_t = df["t"].min()
lists_root_rel = {u: v - base_t for u, v in lists_root.items()}
pt_rel = {u: v - base_t for u, v in pt.items()}
real = chain_scores(pt_rel, lists_root_rel)
rng = np.random.default_rng(0)
null_runs = []
for k in range(30):
    sh = rng.uniform(120, df["rel"].max() - 120)
    # shift only senders' posts, not the reader's own reaction
    sc = Counter()
    for B in users:
        arr = lists_root_rel.get(B); pb = pt_rel.get(B)
        if arr is None or pb is None:
            continue
        for tb in pb:
            i = np.searchsorted(arr, tb) - 1
            if i < 0 or tb - arr[i] > REACT:
                continue
            lo = arr[i - 1] if i >= 1 else arr[i] - 3600
            for A, ts in pt_rel.items():
                if A == B:
                    continue
                tsA = (ts + sh) % (df["rel"].max() + 1)
                if ((tsA > lo) & (tsA <= arr[i])).any():
                    sc[(A, B)] += 1
    null_runs.append(sc)
pairs = [(a, b) for a in users for b in users if a != b]
real_v = np.array([real.get(p_, 0) for p_ in pairs], float)
null_m = np.array([[nr.get(p_, 0) for p_ in pairs] for nr in null_runs], float)
z = (real_v - null_m.mean(0)) / (null_m.std(0) + 1e-9)
truth_v = np.array([true_pairs.get(p_, 0) for p_ in pairs], float)


def auc(score, label):
    pos = score[label > 0]; neg = score[label == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    return float(np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg]))


def spearman(a, b):
    if a.std() == 0 or b.std() == 0:
        return None
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


R["D4b_L0_pair_scores"] = dict(
    pairs=len(pairs), true_pairs=int((truth_v > 0).sum()),
    auc_raw=rnd(auc(real_v, truth_v)), auc_z_vs_shift_null=rnd(auc(z, truth_v)),
    spearman_with_true_message_counts=rnd(spearman(real_v, truth_v)),
    top10_precision=rnd(np.mean(truth_v[np.argsort(-z)[:10]] > 0)))

# 4c L1: opaque path equality. Leaf names are treated as opaque tokens: we only test whether two requests carry the same path.
#   inbox / file : a user lists a private directory D within 10 min after another user wrote into D -> writer talked to lister
#   answer       : a user created path P that another user had probed (empty listing) in the previous 10 min -> prober is the receiver
#   ask          : right after the post the sender starts probing a new path P (empty listing) and a different user later creates P -> creator is the receiver
WIN = 600
list_by_dir = defaultdict(list)
for t_, u_, d_, rs_ in zip(lst["t"], lst["user"], lst["dir"], lst["resp"]):
    list_by_dir[d_].append((t_, u_, rs_))
creators = defaultdict(list)
for t_, u_, d_ in zip(posts["t"], posts["user"], posts["dir"]):
    creators[d_].append((t_, u_))
probes_by_user = defaultdict(list)
for t_, u_, d_, rs_ in zip(lst["t"], lst["user"], lst["dir"], lst["resp"]):
    if rs_ < 200 and d_.startswith(root + "/zzANSWER"):
        probes_by_user[u_].append((t_, d_))
first_probe = {}
for u_, pl in probes_by_user.items():
    for tt, d_ in pl:
        first_probe.setdefault((u_, d_), tt)
l1_rows = []
for _, m in directed1.iterrows():
    kind, snd, rcv, t_ = m["g_msg_kind"], m["user"], m["to"], m["t"]
    pred = set()
    if kind in ("inbox", "file_chunk"):
        D = "/".join(m["dir"].split("/")[:-1])
        pred = {u_ for (tt, u_, rs_) in list_by_dir.get(D, []) if u_ != snd and t_ <= tt <= t_ + WIN}
    elif kind == "answer":
        pred = {u_ for (tt, u_, rs_) in list_by_dir.get(m["dir"], []) if u_ != snd and t_ - WIN <= tt < t_ and rs_ < 200}
    elif kind == "ask":
        for (tt, d_) in probes_by_user.get(snd, []):
            if t_ <= tt <= t_ + 20 and first_probe[(snd, d_)] == tt:   # a brand-new path probed right after the post
                pass
            else:
                continue
            if True:
                for (tc, uc) in creators.get(d_, []):
                    if uc != snd and tc >= tt:
                        pred.add(uc)
    l1_rows.append(dict(kind=kind, phase=m["phase"], hit=rcv in pred, exact=(pred == {rcv}), size=len(pred)))
l1d = pd.DataFrame(l1_rows)
l1 = {}
for k, g in l1d.groupby("kind"):
    l1[k] = dict(messages=len(g), receiver_in_predicted=rnd(g["hit"].mean()), predicted_exactly_the_receiver=rnd(g["exact"].mean()),
                 mean_predicted_size=rnd(g["size"].mean(), 2))
l1["all"] = dict(messages=len(l1d), receiver_in_predicted=rnd(l1d["hit"].mean()), predicted_exactly_the_receiver=rnd(l1d["exact"].mean()))
l1["by_phase"] = {ph: dict(messages=len(g), receiver_in_predicted=rnd(g["hit"].mean()), exact=rnd(g["exact"].mean())) for ph, g in l1d.groupby("phase")}
R["D4c_L1_path_equality"] = l1
# empty-listing probes: PROPFIND on a path that does not exist yet (tiny multistatus), per client
emp = lst[(lst["method"] == "PROPFIND") & (lst["resp"] < 200) & lst["repo"].fillna("").str.endswith("-cache")]
R["D2c_empty_listings"] = dict(rows=len(emp), by_user=emp["user"].value_counts().to_dict(),
                                precision=rnd(emp["board"].mean()) if len(emp) else None,
                                distinct_paths=int(emp["dir"].nunique()))

# how many other users list the root within W seconds after a root post (selectivity of the exposure link)
rp = posts[(posts["parent"] == root) & ~posts["dir"].str.contains("zzFILE_") & posts["g_msg_kind"].isin(["ask", "broadcast", "heartbeat", "answer", "inbox_create"])]
rl = {u: g["t"].to_numpy() for u, g in lst[lst["dir"] == root].groupby("user")}
cand = {}
for W in (10, 30, 60, 300):
    c = [sum(1 for v, a in rl.items() if v != u and ((a > t) & (a <= t + W)).any()) for t, u in zip(rp["t"], rp["user"])]
    cand[f"W={W}s"] = dict(posts=len(c), mean_distinct_readers=rnd(np.mean(c), 2), share_exactly_one_reader=rnd(np.mean([x == 1 for x in c])),
                           share_no_reader=rnd(np.mean([x == 0 for x in c])))
R["D4a_root_post_reader_candidates"] = cand

# ------------------------------------------------------------------ property writes
pw = df[df["rpath"].str.contains(";") | df["method"].isin(["PROPPATCH"]) | df["rpath"].str.contains(r"/api/storage/.*\?properties", regex=True)]
R["D5_property_rows"] = dict(
    rows=len(pw), by_user=pw["user"].value_counts().to_dict(),
    by_method=pw["method"].value_counts().to_dict(),
    in_cache_repo=int(pw["repo"].fillna("").str.endswith("-cache").sum()))

# ------------------------------------------------------------------ other server-side facts used in the report
fa = df[df["method"] == "MKCOL"].groupby(["user", "status"]).size().to_dict()
R["mkcol_status_by_user"] = {f"{u}:{s}": int(n) for (u, s), n in fa.items()}
# does listing response size reveal other clients' posts?  listings of the root by one user, resp bytes vs the post count
root_l = lst[lst["dir"] == root].sort_values("t")
if len(root_l):
    posts_before = np.searchsorted(posts["t"].sort_values().to_numpy(), root_l["t"].to_numpy())
    both = pd.DataFrame({"resp": root_l["resp"].to_numpy(), "posts": posts_before, "user": root_l["user"].to_numpy(), "method": root_l["method"].to_numpy()})
    R["listing_size_vs_posts"] = {m: rnd(g["resp"].corr(g["posts"])) for m, g in both.groupby("method") if len(g) > 3}

json.dump(R, open(outp, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
print(json.dumps(R, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))[:12000])
