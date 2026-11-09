#!/usr/bin/env python3
"""Join Artifactory's server logs to the client-side ground truth.

Inputs   <logs>/artifactory-request.log (+ archived .gz), router-request.log, artifactory-access.log,
         <gt>/*.req.jsonl, <gt>/*.msg.jsonl
Output   <out>/requests.jsonl  one row per client request with server-side fields and ground truth
         <out>/messages.jsonl  ground-truth message ledger (posts and sightings)

The server-side view (what a detector may use) is kept under keys s_*. Ground truth keys are g_*.
The join key is the "lab/<client>/<seq>" token in User-Agent. Detectors never read it.
"""
import glob, gzip, json, os, re, sys
from datetime import datetime, timezone

logs, gt, out = sys.argv[1:4]
os.makedirs(out, exist_ok=True)


def iso(s):
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".")
        frac = (frac + "000000000")[:9]
    else:
        head, frac = s, "000000000"
    dt = datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp() + int(frac) / 1e9


def lines(pattern):
    for f in sorted(glob.glob(pattern)):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", errors="replace") as fh:
            for l in fh:
                yield l.rstrip("\n")


UA = re.compile(r"lab/([\w-]+)/(\d+)")
art = {}   # trace -> row from artifactory-request.log
for l in lines(os.path.join(logs, "artifactory-request*.log*")):
    p = l.split("|")
    if len(p) < 11 or not UA.search(p[10]):
        continue
    art[p[1]] = dict(trace=p[1], s_t_art=iso(p[0]), s_ip=p[2], s_user=p[3], s_method=p[4], s_art_path=p[5],
                     s_status=int(p[6]), s_req_bytes=int(p[7]), s_resp_bytes_art=int(p[8]), s_dur_ms=int(p[9]),
                     lab=UA.search(p[10]).groups())
rt = {}    # trace -> router row
for l in lines(os.path.join(logs, "router-request.log*")):
    if '"RequestPath"' not in l:
        continue
    try:
        d = json.loads(l)
    except ValueError:
        continue
    m = UA.search(d.get("request_User-Agent", ""))
    if not m:
        continue
    rt[d["TraceId"]] = dict(trace=d["TraceId"], s_t=iso(d["StartUTC"]), s_path=d["RequestPath"], s_resp_bytes=d["DownstreamContentSize"],
                            s_dur_ns=d["Duration"], s_addr=d["ClientAddr"], s_router_status=d["DownstreamStatus"],
                            s_router_method=d["RequestMethod"], lab=m.groups())
acc = {}   # trace -> access-log event
ACC = re.compile(r"^(\S+) \[(\w+)\] \[(ACCEPTED|DENIED) (\w+)\]\s+(\S+)\s+for client : (\S+) / (\S+)")
for l in lines(os.path.join(logs, "artifactory-access.log*")):
    m = ACC.match(l)
    if m:
        acc[m[2]] = dict(s_acc_event=f"{m[3]} {m[4]}", s_acc_target=m[5])

truth = {}
for f in glob.glob(os.path.join(gt, "*.req.jsonl")):
    for l in open(f):
        d = json.loads(l)
        d["tag"] = d["client"] + ("-hb" if ".hb." in os.path.basename(f) else "")
        truth[(d["tag"], str(d["seq"]))] = d

rows, orphans = [], 0
for tr, r in rt.items():
    key = r["lab"]
    g = truth.get(key)
    if g is None:
        orphans += 1
        continue
    if abs(g["t_send"] - r["s_t"]) > 2.0:   # same client/seq from an earlier run in the same log files
        orphans += 1
        continue
    a = art.get(tr, {})
    row = {"trace": tr, **{k: v for k, v in r.items() if k not in ("lab",)}}
    row.update({k: v for k, v in a.items() if k not in ("lab", "trace")})
    row.update(acc.get(tr, {}))
    row.update({"g_" + k: v for k, v in g.items()})
    rows.append(row)
rows.sort(key=lambda r: r["s_t"])
with open(os.path.join(out, "requests.jsonl"), "w") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")

n_msgs = 0
with open(os.path.join(out, "messages.jsonl"), "w") as fh:
    for f in sorted(glob.glob(os.path.join(gt, "*.msg.jsonl"))):
        for l in open(f):
            fh.write(l)
            n_msgs += 1
print(f"truth requests {len(truth)}  router rows {len(rt)}  artifactory rows {len(art)}  joined {len(rows)}  "
      f"router-without-truth {orphans}  messages {n_msgs}  access-events {len(acc)}")
joined_keys = {(r["g_tag"], str(r["g_seq"])) for r in rows}
print("truth rows with no router log line:", sum(1 for k in truth if k not in joined_keys))
