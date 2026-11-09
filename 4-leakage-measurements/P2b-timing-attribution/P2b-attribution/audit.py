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
    with path.open() as stream:
        trials = [json.loads(line) for line in stream]
    public = json.loads((folder / f"{split}-provision.json").read_text())
    server = json.loads((folder / f"{split}-server.json").read_text())
    cleanup = json.loads((folder / f"{split}-cleanup.json").read_text())
    check("seal hash", p2b.sha(path) == sealed["sha256"] and sealed["trials"] == len(trials) == p2b.SIZES[split])
    wanted = {f"{split}-{suffix}" for suffix in
              ("provision.json", "schedule.json", "server.json", "server-log-lines.txt", "cleanup.json")}
    check("sealed source and evidence hashes",
          sealed.get("source_manifest_sha256") == p2b.sha(HERE / "SOURCES-SHA256.txt")
          and set(sealed.get("evidence_sha256", {})) == wanted
          and all(p2b.sha(folder / name) == expected for name, expected in sealed.get("evidence_sha256", {}).items()))
    check("cleanup proved", cleanup["absence_proved"] and not cleanup["containers_readback"]
          and not cleanup["networks_readback"] and not cleanup["art_a_p1_networks"])
    check("run identity", server["run_id"] == public["run_id"] == sealed["run_id"])
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
    roles = {"alice", "bob", "gateway", "upstream"} | ({"gateway_bob"} if arm == "pergw" else set())
    check("complete boundary inventory", set(boundary["containers"]) == roles
          and set(boundary["networks"]) == {"alice", "bob", "services"})
    check("retained sandbox boundaries", all(
        r["user"] == "20001:20001" and r["root_read_only"]
        and r["memory"] == r["memory_swap"] == 134217728
        and r["nano_cpus"] == 500000000 and r["pids_limit"] == 48
        and "ALL" in r["cap_drop"] and "no-new-privileges:true" in r["security_options"]
        and not r["forbidden_environment_present"] and r["mounts_read_only"]
        and not any(r["published_ports"].values()) for r in boundary["containers"].values())
        and all(r["internal"] and r["options"].get(
            "com.docker.network.bridge.gateway_mode_ipv4") == "isolated"
            for r in boundary["networks"].values()))
    for role, row in boundary["containers"].items():
        if role in ("alice", "bob"):
            networks = {boundary["networks"][role]["name"]}
        elif role == "upstream":
            networks = {boundary["networks"]["services"]["name"]}
        elif arm == "pergw":
            actor = "bob" if role == "gateway_bob" else "alice"
            networks = {boundary["networks"][k]["name"] for k in (actor, "services")}
        else:
            networks = {r["name"] for r in boundary["networks"].values()}
        check(f"{role} assigned network topology", set(row["attached_networks"]) == networks
              and set(row["network_addresses"]) == networks and all(row["network_addresses"].values()))
        if role in ("alice", "bob"):
            check(f"{role} no host bind", not row["bind_mount_destinations"])
    controls = public["network_positive_controls"]
    required = {("bob", "live peer listener on its own network"),
                ("alice", "same live listener from the other network")}
    required.update((actor, name) for actor in ("alice", "bob")
                    for name in ("assigned gateway route", "direct backend bypass"))
    required.add(("gateway", "backend is reachable from the gateway"))
    if arm == "pergw":
        required.update((actor, "peer gateway route") for actor in ("alice", "bob"))
        required.add(("gateway_bob", "backend is reachable from the gateway"))
    check("live network controls and listener stop", controls["passed"] and controls["listener_stopped"]
          and controls["listener_exit_code"] == 0 and controls["checks"] == len(required)
          and len(controls["records"]) == len(required)
          and {(r["actor"], r["name"]) for r in controls["records"]} == required
          and all(r["connected"] == r["expected_connected"] for r in controls["records"]))
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
                backend[f[10]].append({"trace": f[1], "principal": f[3], "method": f[4],
                                      "path": f[5], "status": int(f[6])})
    markers, forwarded = set(), set()
    for trial, planned in zip(trials, expected):
        tid = trial["trial_id"]
        check(f"{tid} assignment", all(trial[k] == v for k, v in planned.items()))
        check(f"{tid} no failures", not trial["failed_request_markers"])
        recv = plan.receiver_jobs(public, public["run_id"], trial)
        check(f"{tid} receiver grammar", [(r["method"], r["path"], r["marker"]) for r in trial["post"]]
              == [(j["method"], j["path"], j["marker"]) for j in recv])
        active = trial["label"] == 1 and arm != "aa"
        check(f"{tid} sender execution matches label", len(trial["sender_records"]) == (128 if active else 0))
        send = plan.sender_jobs(public, public["run_id"], {**trial, "label": 1}, "separate") if active else []
        check(f"{tid} sender grammar", [(r["method"], r["path"], r["marker"]) for r in trial["sender_records"]]
              == [(j["method"], j["path"], j["marker"]) for j in send])
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
                  and g["status"] == r["status"] and g["body_sha256"] == r["body_sha256"]
                  and g["body_bytes"] == r["body_length"]
                  and dict(g["request_headers"]) == {k.lower(): v for k, v in r["request_headers"].items()})
            check(f"valid client interval and common epoch {m}", r["start_utc_ns"] <= r["end_utc_ns"]
                  and r["schedule_epoch_utc_ns"] == trial["epoch_utc_ns"])
            if arm == "pergw":
                check(f"own-tenant gateway {m}", where[m] == r["actor"])
            if arm == "pad":
                check(f"response floor held {m}", g.get("min_response_ms") == p2b.PAD_MS
                      and g.get("headers_started_utc_ns", 0) - g["start_utc_ns"] >= p2b.PAD_MS * 1_000_000)
            if g["policy_reason"]:
                check(f"blocked request never forwarded {m}", not backend.get(m) and not router.get(m))
                continue
            forwarded.add(m)
            check(f"one backend and router row {m}", len(backend[m]) == len(router[m]) == 1)
            if len(backend[m]) == 1 and len(router[m]) == 1:
                b, ro = backend[m][0], router[m][0]
                start = utc_ns(ro["StartUTC"])
                target = r["path"].partition("?")[0]
                directory = r["method"] == "GET" and target in {
                    f"/{repo}-cache/" for repo in public["repositories"].values()} and b["path"] == "/api/nativeBrowser"
                check(f"backend identity and trace {m}", b["principal"] == g["principal"]
                      and b["method"] == r["method"] and b["status"] == r["status"]
                      and bool(b["trace"]) and b["trace"] == ro.get("TraceId")
                      and (b["path"] == target or directory)
                      and ro["RequestPath"] == "/artifactory" + r["path"]
                      and ro["RequestMethod"] == r["method"] and ro["DownstreamStatus"] == r["status"]
                      and isinstance(ro["Duration"], int) and not isinstance(ro["Duration"], bool)
                      and ro["Duration"] >= 0
                      and r["start_utc_ns"] - 2_000_000 <= start <= start + ro["Duration"] <= r["end_utc_ns"] + 2_000_000)
    check("no unexplained gateway rows", set(gateway) == markers)
    check("no unexplained backend or router rows", set(backend) == set(router) == forwarded)
    return {"arm": arm, "split": split, "trials": len(trials), "checks": checks[0],
            "passed": not failures, "failure_count": len(failures), "failures": failures[:30],
            "seal_sha256": p2b.sha(folder / f"{split}-sealed.json")}


def main():
    data, split = Path(sys.argv[1]), sys.argv[2]
    p2b.check_sources()
    runs = [audit_arm(data / arm, arm, split) for arm in p2b.ARMS]
    out = {"split": split, "passed": all(r["passed"] for r in runs), "runs": runs,
           "audit_source_sha256": p2b.sha(Path(__file__)),
           "source_manifest_sha256": p2b.sha(HERE / "SOURCES-SHA256.txt")}
    (data / f"audit-{split}.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({r["arm"]: (r["passed"], r["checks"], r.get("failures", [])[:3]) for r in runs}))
    sys.exit(0 if out["passed"] else 1)


if __name__ == "__main__":
    main()
