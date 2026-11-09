#!/usr/bin/env python3
"""Join Artifactory's request log with the controller's tool-call log.

  gap-join.py CONTROLLER.jsonl ARTIFACTORY_REQUEST.log CLIENT_IP_PREFIX [--agents agent1=172.30.11.1,...]

For every Artifactory request from a sandbox IP, two attributions are computed:
  time-only : the agent's tool calls whose [t_call_start, t_call_end] window contains the request time.
  issued-by : the subset of those whose command text contains the request's user-agent tag, i.e. the call
              that actually ran the curl. A background loop started by an earlier call has no such call
              after the spawning call returns, even though other calls (e.g. a long sleep) overlap it in time. Request log line format (7.161):
  ts|trace_id|client_ip|user|method|path|status|req_bytes|resp_bytes|duration_ms|user_agent
"""
import json, sys
from datetime import datetime, timezone
ctl, art = sys.argv[1], sys.argv[2]
ips = dict(kv.split("=") for kv in sys.argv[sys.argv.index("--agents") + 1].split(","))
ip2agent = {v: k for k, v in ips.items()}
calls = [json.loads(l) for l in open(ctl)]
calls = [c for c in calls if c["kind"] == "tool_call"]
t0 = min(c["t_call_start_ns"] for c in calls) - 2 * 10**9
t1 = max(c["t_call_end_ns"] for c in calls) + 15 * 10**9
def ns(ts): return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp() * 10**9)
rows = []
for line in open(art):
    p = line.rstrip("\n").split("|")
    if len(p) < 11 or p[2] not in ip2agent: continue
    t = ns(p[0])
    if not t0 <= t <= t1: continue
    a = ip2agent[p[2]]
    win = [c for c in calls if c["agent"] == a and c["t_call_start_ns"] <= t <= c["t_call_end_ns"]]
    iss = [c for c in win if p[10] in c["cmd"] and "setsid" not in c["cmd"]]
    rows.append((p[0], a, p[3], p[4], p[5], p[6], p[9] + "ms", p[10],
                 ",".join(str(c["seq"]) for c in win) or "-", ",".join(str(c["seq"]) for c in iss) or "NONE"))
print(f"{'artifactory_time':26} {'agent':7} {'method':6} {'st':3} {'dur':6} {'user-agent':14} {'time-only (calls running)':26} issued-by")
for r in rows: print(f"{r[0]:26} {r[1]:7} {r[3]:6} {r[5]:3} {r[6]:6} {r[7]:14} {r[8]:26} {r[9]}")
un = [r for r in rows if r[9] == "NONE"]
print(f"\n{len(rows)} Artifactory requests from sandbox IPs; {len(un)} were issued by no tool call "
      f"({sum(1 for r in un if r[8] != '-')} of those still overlap some tool call by time alone)")
