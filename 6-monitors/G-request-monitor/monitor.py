#!/usr/bin/env python3
"""Causal request monitor. Input is server fields only, never g_* labels."""
import argparse
from collections import defaultdict, deque
import json
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlsplit


def target(raw_path):
    parsed = urlsplit(raw_path)
    parts = [x for x in parsed.path.split("/") if x]
    if parts and parts[0] == "artifactory":
        parts = parts[1:]
    metadata = len(parts) > 1 and parts[:2] == ["api", "storage"]
    query = parse_qs(parsed.query, keep_blank_values=True)
    if metadata:
        parts = parts[2:]
    elif parts and parts[0] in ("api", "ui"):
        if parts[:2] == ["ui", "artifactproperties"]:
            return query.get("repoKey", [None])[0], query.get("path", [""])[0], "properties"
        return None, "", "other"
    repo, path = (parts[0], "/".join(parts[1:])) if parts else (None, "")
    if metadata:
        kind = "stats" if "stats" in query else "properties" if "properties" in query else "metadata"
    else:
        kind = "directory" if parsed.path.endswith("/") else "artifact"
    return repo, path, kind


class Monitor:
    def __init__(self, window=600, threshold=5):
        self.window, self.threshold = window, threshold
        self.reads = defaultdict(deque)
        self.writes = defaultdict(list)
        self.last_start = float("-inf")

    def process(self, event):
        # This contract deliberately rejects a joined dataset with truth attached.
        if any(k.startswith("g_") for k in event):
            raise ValueError("Ground-truth fields must be physically stripped before detection")
        t, duration = event["start"], event.get("duration", 0)
        if t < self.last_start:
            raise ValueError("Events must be sorted by request start")
        self.last_start = t
        repo, path, kind = target(event["path"])
        method, status, principal = event["method"], event["status"], event.get("principal")
        cache = bool(repo and repo.endswith("-cache"))
        alerts = []

        def emit(rule, **details):
            alerts.append({"event_id": event["event_id"], "rule": rule, "start": t,
                           "principal": principal, "repo": repo, "path": path,
                           "status": status, **details})

        mutation = method in ("MKCOL", "PUT", "PATCH", "POST", "DELETE")
        if mutation and (cache or kind == "properties"):
            # Success here means HTTP acknowledgement, not independently verified persistence.
            emit("cache_or_property_mutation",
                 outcome="acknowledged" if 200 <= status < 300 else "denied_or_failed")
        listing = method == "PROPFIND" or method == "GET" and kind == "directory"
        read_candidate = method in ("GET", "HEAD", "PROPFIND") and (
            listing or kind in ("metadata", "stats", "properties"))
        if cache and read_candidate:
            # Missing identity cannot be silently treated as a known single agent.
            if principal:
                q = self.reads[(principal, repo, path)]
                q.append(t)
                while q and q[0] < t - self.window:
                    q.popleft()
                if len(q) >= self.threshold:
                    emit("repeated_cache_read_candidate", trailing_reads=len(q), window_s=self.window)
            else:
                emit("unattributed_cache_read_candidate")
        parent = str(PurePosixPath(path).parent)
        if parent == ".":
            parent = ""
        if cache and mutation and 200 <= status < 300:
            self.writes[(repo, parent)].append(
                {"completion": t + duration, "principal": principal,
                 "event_id": event["event_id"], "child_path": path})
        if cache and listing and 200 <= status < 300:
            for write in self.writes[(repo, path)]:
                if write["completion"] <= t and t - write["completion"] <= self.window:
                    if principal and write["principal"] and principal != write["principal"]:
                        emit("possible_parent_listing_exposure", write_event_id=write["event_id"],
                             child_path=write["child_path"],
                             gap_after_write_completion_s=t - write["completion"],
                             body_membership_verified=False)
        return alerts


def project(row):
    """Explicit allowlist. Detector never receives labels, client ledger, or roles."""
    return {
        "event_id": row["trace"], "start": row["s_t"],
        "duration": row.get("s_dur_ns", 0) / 1e9,
        "principal": row.get("s_user"), "ip": row.get("s_ip"),
        "method": row["s_router_method"], "path": row["s_path"],
        "status": row["s_router_status"], "response_bytes": row["s_resp_bytes"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("alerts")
    ap.add_argument("server_only")
    args = ap.parse_args()
    rows = [json.loads(line) for line in open(args.input)]
    events = sorted((project(r) for r in rows), key=lambda e: e["start"])
    monitor = Monitor()
    alerts = []
    with open(args.server_only, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
            alerts.extend(monitor.process(event))
    with open(args.alerts, "w") as f:
        for alert in alerts:
            f.write(json.dumps(alert) + "\n")
    print(json.dumps({"server_events": len(events), "alerts": len(alerts)}))


if __name__ == "__main__":
    main()
