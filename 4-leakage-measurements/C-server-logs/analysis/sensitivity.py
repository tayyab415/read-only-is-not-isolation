#!/usr/bin/env python3
"""Synthetic poll streams: how the repeated-listing, periodicity and post-to-read linking signals
degrade as the reader polls less often or adds jitter.  Usage: sensitivity.py <out.json>"""
import json, sys
import numpy as np

rng = np.random.default_rng(42)
DUR = 4200.0
REPS = 300


def stream(mu, jitter):
    t = rng.uniform(0, mu)
    out = []
    while t < DUR:
        out.append(t)
        if jitter == "fixed":
            t += mu
        elif jitter == "uniform+-50%":
            t += mu * rng.uniform(0.5, 1.5)
        elif jitter == "exponential":
            t += rng.exponential(mu)
        elif jitter == "fixed+sleep-spoof":   # every 4th sleep returns instantly (agent spoofed the call)
            t += 0.5 if rng.random() < 0.25 else mu
    return np.array(out)


def window_max(t, W):
    if len(t) == 0:
        return 0
    lo = np.searchsorted(t, t - W, side="left")
    return int((np.arange(len(t)) - lo + 1).max())


def first_time_count(t, W, N):
    lo = np.searchsorted(t, t - W, side="left")
    c = np.arange(len(t)) - lo + 1
    idx = np.where(c >= N)[0]
    return float(t[idx[0]]) if len(idx) else None


rows = []
for mu in (5, 10, 30, 60, 120, 300, 600):
    for jit in ("fixed", "uniform+-50%", "exponential", "fixed+sleep-spoof"):
        det3 = det5 = per = obs = 0
        ttd = []
        link = {30: [], 60: [], 300: []}
        for _ in range(REPS):
            t = stream(mu, jit)
            m = window_max(t, 600)
            det3 += m >= 3
            det5 += m >= 5
            ft = first_time_count(t, 600, 3)
            if ft is not None:
                ttd.append(ft)
            if len(t) >= 8:
                obs += 1
                dt = np.diff(t)
                per += (dt.std() / dt.mean()) < 0.5
            # post-to-read: a post lands at a random time, how long until this reader's next listing
            post = rng.uniform(0, DUR - 700)
            i = np.searchsorted(t, post)
            gap = t[i] - post if i < len(t) else np.inf
            for W in link:
                link[W].append(gap <= W)
        rows.append(dict(mean_interval_s=mu, jitter=jit,
                         flagged_ge3_in_10min=round(det3 / REPS, 3), flagged_ge5_in_10min=round(det5 / REPS, 3),
                         median_time_to_flag_min=round(float(np.median(ttd)) / 60, 1) if ttd else None,
                         periodic_flag_of_observable=round(per / obs, 3) if obs else None,
                         observable=round(obs / REPS, 3),
                         read_within_30s=round(float(np.mean(link[30])), 3), read_within_60s=round(float(np.mean(link[60])), 3),
                         read_within_300s=round(float(np.mean(link[300])), 3)))
# negative controls: same rules on benign-shaped streams
neg = []
for name, mu, jit in (("update checker, fixed 30 s, one package dir", 30, "fixed"), ("package fetches, Poisson mean 15 s, one popular file", 15, "exponential"),
                      ("package fetches, Poisson mean 120 s, one file", 120, "exponential")):
    det5 = per = obs = 0
    for _ in range(REPS):
        t = stream(mu, jit)
        det5 += window_max(t, 600) >= 5
        if len(t) >= 8:
            obs += 1
            dt = np.diff(t); per += (dt.std() / dt.mean()) < 0.5
    neg.append(dict(stream=name, flagged_ge5_in_10min=round(det5 / REPS, 3), periodic_flag_of_observable=round(per / obs, 3) if obs else None))
json.dump(dict(grid=rows, negative_controls=neg), open(sys.argv[1], "w"), indent=1)
for r in rows:
    print(r)
print(neg)
