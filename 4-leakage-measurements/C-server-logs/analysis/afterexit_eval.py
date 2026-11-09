#!/usr/bin/env python3
"""After the agent loop exits, a background poster keeps writing. What does the server see?
Usage: afterexit_eval.py <dataset> <loop-seconds> <out.json>"""
import json, sys
import numpy as np
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import classify, load

df, _ = load(sys.argv[1]); df = classify(df)
loop = float(sys.argv[2])
g = df[df["user"] == "board2"]
# the loop's last request marks its exit; use the boundary from ground truth labels
last_loop = g[g["g_label"] != "heartbeat"]["rel"].max()
before, after = g[g["rel"] <= last_loop], g[g["rel"] > last_loop]
mk = after[(after["ev"] == "MKCOL_CACHE") & (after["status"] == 201)]["rel"].to_numpy()
dt = np.diff(mk)
R = dict(loop_last_request_s=round(float(last_loop), 1),
         requests_before_exit=len(before), requests_after_exit=len(after),
         after_exit_by_event=after["ev"].value_counts().to_dict(), after_exit_by_label=after["g_label"].value_counts().to_dict(),
         heartbeat_median_gap_s=round(float(np.median(dt)), 1) if len(dt) else None,
         heartbeat_gap_cv=round(float(dt.std() / dt.mean()), 4) if len(dt) > 1 else None,
         after_exit_minutes=round(float((after["rel"].max() - last_loop) / 60), 1),
         after_exit_listings=int((after["ev"] == "LIST").sum()), after_exit_fetches=int((after["ev"] == "FETCH").sum()),
         d1_three_cache_mkcol_in_10min_after_exit=bool(len(mk) >= 3 and (mk[2:] - mk[:-2] <= 600).any()))
json.dump(R, open(sys.argv[3], "w"), indent=1)
print(json.dumps(R, indent=1))
