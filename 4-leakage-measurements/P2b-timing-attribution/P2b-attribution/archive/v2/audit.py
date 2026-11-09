#!/usr/bin/env python3
"""P2b native audit. Every client request must join exactly one gateway record and,
when forwarded, exactly one Artifactory request-log row and one router row with the
same trace ID. Runs on the labhost or on the Mac against copied data.

    python3 audit.py DATA SPLIT      # writes DATA/audit-SPLIT.json; exit 1 on any failure
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "P2-residual"))
import plan  # noqa: E402
import p2b  # noqa: E402


def utc_ns(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9)


def audit_arm(folder, arm, split):
    failures, checks = [], [0]

    def check(name, ok):
        checks[0] += 1
        if not ok:
            failures.append(name)

    sealed = json.loads((folder / f"{split}-sealed.json").read_text())
    if not sealed.get("complete"):
        return {"arm": arm, "split": split, "passed": False, "failures": ["incomplete collection"], "checks": 1}
    path = folder / f"{split}.jsonl"
    trials = [json.loads(line) for line in path.open()]
    public = json.loads((folder / f"{split}-provision.json").read_text())
    server = json.loads((folder / f"{split}-server.json").read_text())
    cleanup = json.loads((folder / f"{split}-cleanup.json").read_text())
    check("seal hash", p2b.sha(path) == sealed["sha256"] and sealed["trials"] == len(trials) == p2b.SIZES[split])
    check("cleanup proved", cleanup["absence_proved"])
    expected = p2b.schedule(arm, split)
    check("exact schedule", json.loads((folder / f"{split}-schedule.json").read_text()) == expected)
    check("balanced labels", Counter(t["label"] for t in trials) == {0: len(trials) // 2, 1: len(trials) // 2})
    check("arm configuration", public["arm"] == arm and public["storage_blocked"]
          and public["negative_cache_period_seconds"] == 1800 and len(set(public["repositories"].values())) == 2
          and public["pad_ms"] == (p2b.PAD_MS if arm == "pad" else 0))
    if arm == "pergw":
        check("pergw negative route checks", public["negative_route_checks"]
              and all(public["negative_route_checks"].values()))
    boundary = public["boundary_readback"]
    ip = {a: boundary["containers"][a]["network_addresses"][boundary["networks"][a]["name"]] for a in ("alice", "bob")}
    gw_rows = {"alice": server["gateway_log"], "bob": server.get("gateway_bob_log", server["gateway_log"])}
    if arm != "pergw":
        gw_rows["bob"] = server["gateway_log"]
    gateway = defaultdict(list)
    where = {}
    for actor_gw, rows in (("alice", gw_rows["alice"]),) + ((("bob", gw_rows["bob"]),) if arm == "pergw" else ()):
        for row in rows:
            if row.get("marker"):
                gateway[row["marker"]].append(row)
                where[row["marker"]] = actor_gw
    backend, router = defaultdict(list), defaultdict(list)
    for line in (folder / f"{split}-server-log-lines.txt").read_text().splitlines():
        if line.startswith("{"):
            row = json.loads(line)
            router[row.get("request_User-Agent")].append(row)
        else:
            f = line.split("|")
            if len(f) >= 11:
                backend[f[10]].append({"trace": f[1], "principal": f[3], "method": f[4], "status": int(f[6])})
    markers = set()
    for trial, planned in zip(trials, expected):
        tid = trial["trial_id"]
        check(f"{tid} assignment", all(trial[k] == v for k, v in planned.items()))
        check(f"{tid} no failures", not trial["failed_request_markers"])
        recv = plan.receiver_jobs(public, public["run_id"], trial)
        check(f"{tid} receiver grammar", [(r["method"], r["path"], r["marker"]) for r in trial["post"]]
              == [(j["method"], j["path"], j["marker"]) for j in recv])
        active = trial["label"] == 1 and arm != "aa"
        check(f"{tid} sender execution matches label", len(trial["sender_records"]) == (128 if active else 0))
        for r in trial["post"] + trial["sender_records"]:
            m = r["marker"]
            check(f"unique marker {m}", m not in markers)
            markers.add(m)
            check(f"one gateway row {m}", len(gateway[m]) == 1)
            if len(gateway[m]) != 1:
                continue
            g = gateway[m][0]
            check(f"gateway identity/target/status/body {m}",
                  g["principal"] == public["users"][r["actor"]] and g["client_ip"] == ip[r["actor"]]
                  and g["method"] == r["method"] and g["path"] == "/artifactory" + r["path"]
                  and g["status"] == r["status"] and g["body_sha256"] == r["body_sha256"])
            if arm == "pergw":
                check(f"own-tenant gateway {m}", where[m] == r["actor"])
            if arm == "pad":
                check(f"response floor held {m}", g.get("min_response_ms") == p2b.PAD_MS
                      and g["end_utc_ns"] - g["start_utc_ns"] >= p2b.PAD_MS * 1_000_000)
            if g["policy_reason"]:
                check(f"blocked request never forwarded {m}", not backend.get(m) and not router.get(m))
                continue
            check(f"one backend and router row {m}", len(backend[m]) == len(router[m]) == 1)
            if len(backend[m]) == 1 and len(router[m]) == 1:
                b, ro = backend[m][0], router[m][0]
                start = utc_ns(ro["StartUTC"])
                check(f"backend identity and trace {m}", b["principal"] == g["principal"]
                      and b["method"] == r["method"] and b["status"] == r["status"]
                      and b["trace"] == ro.get("TraceId")
                      and r["start_utc_ns"] - 2_000_000 <= start <= start + ro["Duration"] <= r["end_utc_ns"] + 2_000_000)
    check("no unexplained gateway rows", set(gateway) == markers)
    return {"arm": arm, "split": split, "trials": len(trials), "checks": checks[0],
            "passed": not failures, "failure_count": len(failures), "failures": failures[:30]}


def main():
    data, split = Path(sys.argv[1]), sys.argv[2]
    runs = [audit_arm(data / arm, arm, split) for arm in p2b.ARMS]
    out = {"split": split, "passed": all(r["passed"] for r in runs), "runs": runs}
    (data / f"audit-{split}.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({r["arm"]: (r["passed"], r["checks"], r.get("failures", [])[:3]) for r in runs}))
    sys.exit(0 if out["passed"] else 1)


if __name__ == "__main__":
    main()
