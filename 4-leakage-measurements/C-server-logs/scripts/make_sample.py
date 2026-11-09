#!/usr/bin/env python3
"""Cut a readable sample out of the raw server logs: lines from two 3-minute windows (one per phase) of the
lab run, plus the complete small logs.  Usage: make_sample.py <logs-dir> <t0-epoch> <phase-split-s> <out-dir>"""
import glob, gzip, os, re, shutil, sys
from datetime import datetime, timezone

logs, t0, split, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
os.makedirs(out, exist_ok=True)
wins = [(t0 + 120, t0 + 300), (t0 + split + 120, t0 + split + 300)]


def ts(line):
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z", line)
    if not m:
        m = re.search(r'"StartUTC":"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)', line)
    if not m:
        return None
    return datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc).timestamp()


def lines(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", errors="replace") as fh:
        yield from fh


for name in ("artifactory-request.log", "router-request.log", "artifactory-access.log"):
    n = 0
    with open(os.path.join(out, name + ".sample"), "w") as o:
        for f in sorted(glob.glob(os.path.join(logs, name + "*"))):
            for l in lines(f):
                t = ts(l)
                if t and any(a <= t <= b for a, b in wins) and "lab/" in l or (name == "artifactory-access.log" and t and any(a <= t <= b for a, b in wins)):
                    o.write(l if l.endswith("\n") else l + "\n"); n += 1
    print(name, n, "lines")
for name in ("artifactory-request-out.log", "access-audit.log", "access-security-audit.log"):
    src = os.path.join(logs, name)
    if os.path.exists(src):
        with open(os.path.join(out, name + ".head"), "w") as o:
            for i, l in enumerate(lines(src)):
                if i >= 400:
                    break
                o.write(l if l.endswith("\n") else l + "\n")
