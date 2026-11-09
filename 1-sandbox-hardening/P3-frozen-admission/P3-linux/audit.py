"""Independent checks of retained Linux utility tasks and serving records."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
P3 = HERE.parent / "P3-mirror"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_ns(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9)


def marked(records):
    result = defaultdict(list)
    for row in records:
        if row.get("marker"):
            result[row["marker"]].append(row)
    return result


def parse_backend(lines):
    backend, router = defaultdict(list), defaultdict(list)
    for line in lines:
        if line.startswith("{"):
            row = json.loads(line)
            router[row.get("request_User-Agent")].append(row)
        else:
            fields = line.split("|")
            if len(fields) >= 11:
                backend[fields[10]].append({"principal": fields[3], "trace_id": fields[1], "method": fields[4],
                    "path": fields[5], "status": int(fields[6]), "start_utc_ns": utc_ns(fields[0])})
    return backend, router


def trace_interval(art, route, event):
    duration = route.get("Duration")
    start = utc_ns(route["StartUTC"])
    return (bool(art["trace_id"]) and art["trace_id"] == route.get("TraceId")
            and isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0
            and event["start_utc_ns"] - 2_000_000 <= start <= start + duration
            <= event["end_utc_ns"] + 2_000_000)


def task_downloads(record, events, manifest, host):
    """Every declared pip download must match delivered, admitted wheel bytes."""
    entries = {f"/files/{row['sha256']}/{row['filename']}": row for row in manifest["entries"]}
    downloads = record.get("pip_report", {}).get("install", [])
    file_events = [row for row in events if row["backend_target"] is not None]
    wanted, seen = set(), set()
    for item in downloads:
        url = urlsplit(item["download_info"]["url"])
        prefix = f"/task/{record['task_id']}"
        route = url.path.removeprefix(prefix)
        entry = entries.get(route)
        if url.scheme != "http" or url.hostname != host or url.port != 8000 \
                or not url.path.startswith(prefix + "/files/") or url.query or url.fragment or not entry:
            return False
        name = re.sub(r"[-_.]+", "-", item["metadata"]["name"]).lower()
        if name != entry["canonical"] or item["metadata"]["version"] != entry["version"] \
                or item["download_info"]["archive_info"].get("hashes", {}).get("sha256") != entry["sha256"]:
            return False
        wanted.add(url.path)
    for event in file_events:
        prefix = f"/task/{record['task_id']}"
        entry = entries.get(event["path"].removeprefix(prefix))
        if not entry or event["method"] != "GET" or event["status"] != 200 \
                or event["body_sha256"] != entry["sha256"] or event["body_bytes"] != entry["bytes"] \
                or event["backend_body_sha256"] != entry["sha256"]:
            return False
        seen.add(event["path"])
    return bool(wanted) and wanted == seen


def audit_arm(folder, mode, freeze):
    failures, checks = [], 0

    def check(name, condition):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)

    seal = json.loads((folder / "sealed.json").read_text())
    tasks = [json.loads(line) for line in (folder / "tasks.jsonl").read_text().splitlines()] \
        if (folder / "tasks.jsonl").exists() else []
    if not seal.get("complete"):
        return {"mode": mode, "passed": False, "checks": 1, "completed": len(tasks),
                "scheduled": 28, "failure_count": 1, "failures": ["Incomplete arm; retain all scheduled tasks."]}
    prepared = json.loads((folder / "prepared.json").read_text())
    server = json.loads((folder / "server.json").read_text())
    cleanup = json.loads((folder / "cleanup.json").read_text())
    manifest = json.loads((P3 / "data/manifest.json").read_text())
    mutable = mode == "separate"
    run_id = f"p3u-m1-{mode}"
    check("exact sealed files", set(seal["sha256"]) == {
        "tasks.jsonl", "prepared.json", "server.json", "server-log-lines.txt", "cleanup.json"}
        and all(sha(folder / name) == expected for name, expected in seal["sha256"].items()))
    check("source freeze", seal["source_freeze_sha256"] == sha(HERE / "UTILITY-FROZEN.json"))
    check("complete schedule and identity", seal["scheduled"] == seal["completed"] == len(tasks) == 28
        and prepared["schedule"] == freeze["tasks"] and prepared["completed_tasks"] == 28
        and prepared["run_id"] == server["run_id"] == seal["run_id"] == run_id)
    check("P1 gate retained", prepared["p1_gate_sha256"] == sha(HERE.parent / "P2-residual/ALLOW-COLLECTION.json")
        and all(prepared["p1_gate"].get(key) is True for key in (
            "p1_validated", "known_statistics_validated", "known_negative_cache_validated",
            "p1_causal_audit_passed", "p1_nulls_passed")))
    check("cleanup proved", cleanup["absence_proved"] and seal["cleanup_absence_proved"]
        and not cleanup["containers_readback"] and not cleanup["networks_readback"] and not cleanup["art_a_p1_networks"])
    boundary = prepared["boundary_readback"]
    roles = {"alice", "bob", "adapter_alice", "adapter_bob"} | (
        {"gateway", "upstream"} if mutable else {"mirror_alice", "mirror_bob"})
    networks = {"alice", "bob", "services"} if mutable else {"alice", "bob"}
    check("exact runtime inventory", set(boundary["containers"]) == roles and set(boundary["networks"]) == networks)
    for role, row in boundary["containers"].items():
        memory = 268435456 if role in ("alice", "bob") else 134217728
        attached = {boundary["networks"][key]["name"] for key in (
            ("alice", "bob", "services") if role == "gateway" else
            ("services",) if role == "upstream" else (role.split("_")[-1],))}
        check(f"{role} retained limits and origin", row["user"] == "20001:20001" and row["root_read_only"]
            and row["memory"] == row["memory_swap"] == memory and row["nano_cpus"] == 500000000
            and row["pids_limit"] == 48 and "ALL" in row["cap_drop"]
            and "no-new-privileges:true" in row["security_options"]
            and not row["forbidden_environment_present"] and not any(row["published_ports"].values())
            and row["mounts_read_only"] and set(row["network_addresses"]) == attached
            and all(row["network_addresses"].values()))
        if role in ("alice", "bob"):
            check(f"{role} private utility storage", not row["bind_mount_destinations"]
                and "size=128m" in row["tmpfs"].get("/tmp", ""))
    check("isolated internal networks", all(row["internal"] and row["options"].get(
        "com.docker.network.bridge.gateway_mode_ipv4") == "isolated" for row in boundary["networks"].values()))
    controls = prepared["network_positive_controls"]
    expected_controls = {("bob", "live peer listener on its own network"),
                         ("alice", "same live listener from the other network")}
    for actor in ("alice", "bob"):
        expected_controls.add((actor, "assigned gateway route" if mutable else "assigned serving route"))
        expected_controls.add((actor, "direct backend bypass" if mutable else "peer serving route"))
    if mutable:
        expected_controls.add(("gateway", "backend is reachable from the gateway"))
    check("complete live network controls", controls["passed"] and controls["listener_stopped"]
        and controls["listener_exit_code"] == 0
        and {(row["actor"], row["name"]) for row in controls["records"]} == expected_controls
        and len(controls["records"]) == len(expected_controls)
        and all(row["connected"] == row["expected_connected"] for row in controls["records"]))
    controls = prepared["adapter_route_controls"]
    check("complete adapter TCP controls", controls["passed"] and len(controls["records"]) == 4
        and {(row["actor"], row["name"]) for row in controls["records"]} ==
            {(actor, name) for actor in ("alice", "bob") for name in ("assigned index adapter", "peer index adapter")}
        and all(row["connected"] == row["expected_connected"] for row in controls["records"]))
    direct = prepared["direct_http_controls"]
    check("direct client HTTP rejected", direct["passed"] and len(direct["records"]) == 2
        and {row["actor"] for row in direct["records"]} == {"alice", "bob"}
        and all(row["status"] == row["expected_status"] == 403 for row in direct["records"]))
    gateway = marked(server.get("gateway_log", []))
    backend, router = parse_backend((folder / "server-log-lines.txt").read_text().splitlines())
    mirror, wire = {}, {}
    if not mutable:
        for actor, data in server["tenants"].items():
            mirror.update(marked(data["response_log"]))
            wire.update(marked(data["wire_log"]))
        check("no immutable upstream work", server["upstream_requests"] == 0)
    expected_forwarded, expected_rejected, expected_admission = set(), set(), set()

    def served(event, actor, origin_role, expected_principal, body_digest, body_bytes, blocked=False):
        marker = event["marker"]
        service_rows = gateway[marker] if mutable else wire.get(marker, [])
        check(f"{marker} one serving request", len(service_rows) == 1)
        if len(service_rows) != 1:
            return
        row = service_rows[0]
        path = event.get("backend_target") or event["path"]
        origin = boundary["containers"][origin_role]["network_addresses"][boundary["networks"][actor]["name"]] \
            if origin_role != "host" else None
        check(f"{marker} serving identity and full target", row["method"] == event["method"]
            and row["path"] == path and row["status"] == event["status"]
            and (origin is None or row["client_ip"] == origin)
            and row["principal"] == (None if blocked else expected_principal))
        if blocked:
            expected_rejected.add(marker)
            check(f"{marker} rejected without package/backend response", not backend[marker] and not router[marker]
                and (mutable and row["policy_reason"] == "origin-identity"
                     or not mutable and not row["origin_permitted"] and not mirror.get(marker)))
            return
        expected_forwarded.add(marker)
        if mutable:
            check(f"{marker} gateway delivered bytes", row["policy_reason"] is None
                and row["body_sha256"] == body_digest and row["body_bytes"] == body_bytes)
            check(f"{marker} one Artifactory and router request", len(backend[marker]) == len(router[marker]) == 1)
            if len(backend[marker]) == len(router[marker]) == 1:
                art, route = backend[marker][0], router[marker][0]
                check(f"{marker} Artifactory identity and target", art["principal"] == expected_principal
                    and art["method"] == event["method"] and art["path"] == path.removeprefix("/artifactory")
                    and art["status"] == event["status"] and route["RequestPath"] == path
                    and route["RequestMethod"] == event["method"] and route["DownstreamStatus"] == event["status"])
                check(f"{marker} backend trace and complete router interval", trace_interval(art, route, event))
        else:
            check(f"{marker} one private response", len(mirror.get(marker, [])) == 1)
            if len(mirror.get(marker, [])) == 1:
                response = mirror[marker][0]
                check(f"{marker} private bytes and no fetch", row["origin_permitted"]
                    and response["method"] == event["method"] and response["path"] == path
                    and response["status"] == event["status"] and response["body_sha256"] == body_digest
                    and response["body_bytes"] == body_bytes and response["upstream_requests"] == 0)

    users = prepared["initial_provision"]["users"]
    for row in direct["records"]:
        served(row, row["actor"], row["actor"], users[row["actor"]], row["body_sha256"], row["body_bytes"], blocked=True)
    if mutable:
        admissions = prepared["trusted_package_prewarm"]
        wanted = {(actor, row["sha256"]) for actor in ("alice", "bob") for row in manifest["entries"]}
        check("all trusted admission fills", len(admissions) == 48
            and {(row["actor"], row["expected_sha256"]) for row in admissions} == wanted
            and all(row["status"] == 200 and row["body_sha256"] == row["expected_sha256"]
                    and row["body_bytes"] == row["expected_bytes"] for row in admissions))
        for row in admissions:
            marker = row["marker"]
            expected_admission.add(marker)
            check(f"{marker} trusted fill bypasses untrusted gateway", not gateway.get(marker)
                and len(backend[marker]) == len(router[marker]) == 1)
            if len(backend[marker]) == len(router[marker]) == 1:
                art, route = backend[marker][0], router[marker][0]
                check(f"{marker} trusted fill identity and target", art["principal"] == row["principal"] == users["prefetch"]
                    and art["method"] == route["RequestMethod"] == row["method"] == "GET"
                    and art["status"] == route["DownstreamStatus"] == row["status"] == 200
                    and art["path"] == row["path"].removeprefix("/artifactory")
                    and route["RequestPath"] == row["path"])
                check(f"{marker} trusted fill trace and complete router interval", trace_interval(art, route, row))
        upstream = server["package_upstream_wire_log"]
        responses = server["package_upstream_response_log"]
        admitted_paths = {f"/files/{row['sha256']}/{row['filename']}" for row in manifest["entries"]}
        origin_ip = prepared["package_source_setup"]["artifactory_origin_ip"]
        check("trusted source serves only fixed admitted content", bool(upstream)
            and len(upstream) == len(responses)
            and all(row["origin_permitted"] and row["principal"] == "trusted-admission"
                    and row["client_ip"] == origin_ip
                    and row["method"] in ("GET", "HEAD") and row["path"] in admitted_paths
                    and row["status"] == 200 for row in upstream)
            and all(row["upstream_requests"] == 0 for row in responses))
        catalog = {f"/files/{row['sha256']}/{row['filename']}": row for row in manifest["entries"]}
        for event, response in zip(sorted(upstream, key=lambda row: row["start_utc_ns"]),
                                   sorted(responses, key=lambda row: row["start_utc_ns"])):
            entry = catalog.get(event["path"])
            if entry:
                check("trusted source response bytes and interval", event["method"] == response["method"]
                    and event["path"] == response["path"] and response["status"] == 200
                    and event["start_utc_ns"] <= response["start_utc_ns"] <= response["end_utc_ns"] <= event["end_utc_ns"]
                    and response["body_sha256"] == (entry["sha256"] if response["method"] == "GET"
                        else hashlib.sha256(b"").hexdigest())
                    and response["body_bytes"] == (entry["bytes"] if response["method"] == "GET" else 0))
    all_events = [event for events in server["adapter_logs"].values() for event in events]
    check("unique adapter markers", len({row["marker"] for row in all_events}) == len(all_events))
    for record, scheduled in zip(tasks, freeze["tasks"]):
        tid, actor = record["task_id"], record["actor"]
        check(f"{tid} exact task and utility evidence", all(record.get(key) == value for key, value in scheduled.items())
            and record["platform"] == "linux" and record["check_source_sha256"] ==
                freeze["import_sha256"]["P3-mirror/utility.py"]
            and record["check_source_removed"] and record["temporary_files_removed"]
            and record["elapsed_s"] >= 0 and record["end_utc_ns"] >= record["start_utc_ns"])
        events = [row for row in all_events if row["task_id"] == tid]
        principal = users[actor]
        client_ip = boundary["containers"][actor]["network_addresses"][boundary["networks"][actor]["name"]]
        check(f"{tid} every request retained inside task", bool(events) and all(row["actor"] == actor
            and row["principal"] == principal and row["client_ip"] == client_ip
            and row["adapter_edition"] == 1 and row["credential_supplied_to_client"] is False
            and record["start_utc_ns"] <= row["start_utc_ns"] <= row["end_utc_ns"] <= record["end_utc_ns"]
            and row["status"] < 500 for row in events))
        if record["passed"]:
            check(f"{tid} success requires function and verified downloads", record["pip_exit_code"] ==
                record["function_exit_code"] == 0 and record["all_downloads_admitted"]
                and task_downloads(record, events, manifest, boundary["containers"][f"adapter_{actor}"]["name"])
                and json.loads(record["function_output"]) == {"function_passed": True})
        for event in events:
            if event["backend_target"]:
                served(event, actor, f"adapter_{actor}", principal, event["body_sha256"], event["body_bytes"])
    check("adapter task coverage", {row["task_id"] for row in all_events} <= {row["task_id"] for row in tasks})
    if mutable:
        check("complete mutable serving ledger coverage", set(gateway) == expected_forwarded | expected_rejected
            and {key for key, values in backend.items() if values} ==
                {key for key, values in router.items() if values} == expected_forwarded | expected_admission)
    else:
        check("complete private serving ledger coverage", set(wire) == expected_forwarded | expected_rejected
            and set(mirror) == expected_forwarded)
    check("success count preserved", seal["passed"] == sum(row["passed"] for row in tasks))
    return {"mode": mode, "passed": not failures, "checks": checks, "scheduled": 28, "completed": len(tasks),
            "successful_tasks": sum(row["passed"] for row in tasks), "failure_count": len(failures),
            "failures": failures[:40], "forwarded_requests": len(expected_forwarded),
            "rejected_controls": len(expected_rejected)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    args = parser.parse_args()
    freeze = json.loads((HERE / "UTILITY-FROZEN.json").read_text())
    for name, expected in freeze["sha256"].items():
        if sha(HERE / name) != expected:
            raise SystemExit("Frozen utility analysis source changed")
    for name, expected in freeze["import_sha256"].items():
        if sha(HERE.parent / name) != expected:
            raise SystemExit("Frozen utility import changed")
    policy = json.loads((HERE.parent / "P2-residual/SOURCES-FROZEN.json").read_text())
    for name, expected in policy["sha256"].items():
        if sha(HERE.parent / "P2-residual" / name) != expected:
            raise SystemExit("Imported P2 implementation changed")
    results = [audit_arm(args.data / mode, mode, freeze) for mode in freeze["arms"]]
    images = [json.loads((args.data / mode / "prepared.json").read_text())["boundary_readback"]["containers"][actor]["image_id"]
              for mode in freeze["arms"] for actor in ("alice", "bob")]
    output = {"passed": all(row["passed"] for row in results) and len(set(images)) == 1,
              "identical_client_image": len(set(images)) == 1, "arms": results,
              "audit_source_sha256": sha(Path(__file__)),
              "sealed_sha256": {mode: sha(args.data / mode / "sealed.json") for mode in freeze["arms"]}}
    (args.data / "audit-results.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
