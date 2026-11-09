#!/usr/bin/env python3
"""Snapshot sanitized A logs and correlate every recorded test to each log."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote
from common import CREDS, LAB, OUT, redact

logroot = LAB / "var/log"
names = subprocess.check_output(["sudo", "find", str(logroot), "-type", "f"],
                               text=True).splitlines()
private_values = []
for keyfile in ["var/etc/security/master.key", "var/etc/security/join.key"]:
    p = LAB / keyfile
    try:
        private_values.append(subprocess.check_output(["sudo", "cat", str(p)],
                                                     text=True).strip())
    except subprocess.CalledProcessError:
        pass
logs = {}
(OUT / "logs").mkdir(exist_ok=True)
inventory = []
for name in sorted(names):
    p = Path(name)
    if not (".log" in p.name or "request" in p.name):
        continue
    raw = subprocess.check_output(["sudo", "cat", name], text=True, errors="replace")
    text = redact(raw)
    for value in private_values:
        if value:
            text = text.replace(value, "<server-key-redacted>")
    text = re.sub(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                  "<jwt-redacted>", text)
    relative = str(p.relative_to(logroot))
    target = OUT / "logs" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    logs[relative] = text.splitlines()
    inventory.append({"name": relative, "bytes_before_redaction": len(raw.encode()),
                      "lines": len(logs[relative])})
(OUT / "log-inventory.json").write_text(json.dumps(inventory, indent=2))
requests = [json.loads(line) for line in (OUT / "requests.jsonl").read_text().splitlines()]
primary = logs.get("artifactory-request.log", [])
primary_by_id = {}
for i, line in enumerate(primary, 1):
    if "|art-a-research/" in line:
        label = line.split("|art-a-research/", 1)[1]
        primary_by_id.setdefault(label, []).append((i, line))
router_by_id = {}
for i, line in enumerate(logs.get("router-request.log", []), 1):
    try:
        value = json.loads(line)
    except ValueError:
        continue
    agent = value.get("request_User-Agent", "")
    if agent.startswith("art-a-research/"):
        router_by_id.setdefault(agent.removeprefix("art-a-research/"), []).append(
            (i, line, value.get("TraceId")))
trace_index = {}
for name, lines in logs.items():
    for number, line in enumerate(lines, 1):
        for trace in set(re.findall(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", line)):
            trace_index.setdefault(trace, []).append((name, number, line))
(OUT / "log-matches").mkdir(exist_ok=True)
match_index = []
for record in requests:
    label = record["id"]
    base = primary_by_id.get(label, [])
    traces = sorted(set([line.split("|")[1] for _, line in base] +
                        [trace for _, _, trace in router_by_id.get(label, []) if trace]))
    matched = {}
    for trace in traces:
        for name, number, line in trace_index.get(trace, []):
            matched.setdefault(name, {})[number] = line
    for number, line in base:
        matched.setdefault("artifactory-request.log", {})[number] = line
    for number, line, _ in router_by_id.get(label, []):
        matched.setdefault("router-request.log", {})[number] = line
    # The access log usually omits request IDs for MKCOL and property writes.
    # Only accept a same-path event inside this request's time interval.
    if "end_utc" in record:
        start = dt.datetime.fromisoformat(record["start_utc"]) - dt.timedelta(milliseconds=100)
        end = dt.datetime.fromisoformat(record["end_utc"]) + dt.timedelta(milliseconds=100)
        path = unquote(record["path"].split("?", 1)[0]).removeprefix("/artifactory/")
        for number, line in enumerate(logs.get("artifactory-access.log", []), 1):
            if path and path in line:
                try:
                    stamp = dt.datetime.fromisoformat(line[:24].replace("Z", "+00:00"))
                except ValueError:
                    continue
                if start <= stamp <= end:
                    matched.setdefault("artifactory-access.log", {})[number] = line
    report = {"request_id": label, "server_trace_ids": traces,
              "logs": {name: [{"line_number": n, "line": line}
                       for n, line in sorted(matched.get(name, {}).items())]
                       for name in logs}}
    (OUT / "log-matches" / (label + ".json")).write_text(json.dumps(report, indent=2))
    match_index.append({"id": label, "server_trace_ids": traces,
                       "artifactory_request_log_present": bool(base),
                       "matches": {name: len(lines) for name, lines in matched.items()}})
(OUT / "log-match-index.json").write_text(json.dumps(match_index, indent=2))
(OUT / "collection-utc.txt").write_text(dt.datetime.now(dt.timezone.utc).isoformat())
print("Collected", len(logs), "logs;", len(requests), "requests;",
      sum(bool(x["server_trace_ids"]) for x in match_index), "with request-log matches")
