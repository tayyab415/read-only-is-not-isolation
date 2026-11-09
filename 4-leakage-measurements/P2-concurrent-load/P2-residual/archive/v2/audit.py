"""Audit P2 assignments, timed requests, and trusted service joins."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

import plan
import service


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def utc_ns(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9)


def lower_headers(values):
    return {name.lower(): value for name, value in (values.items() if isinstance(values, dict) else values)}


def audit_split(folder, configuration, split, smoke=False):
    failures, checks = [], 0

    def check(name, condition):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)

    public = json.loads((folder / f"{split}-provision.json").read_text())
    server = json.loads((folder / f"{split}-server.json").read_text())
    sealed = json.loads((folder / f"{split}-sealed.json").read_text())
    cleanup = json.loads((folder / f"{split}-cleanup.json").read_text())
    path = folder / f"{split}.jsonl"
    trials = rows(path) if path.exists() else []
    if not sealed["complete"]:
        return {"configuration": configuration, "split": split, "trials": len(trials),
                "checks": 1, "passed": False, "failure_count": 1, "incomplete": True,
                "failures": ["Collection is incomplete; preserve the scheduled denominator."]}
    count = 8 if smoke else (300 if split == "calibration" else 1000)
    check("complete split", sealed["complete"] and len(trials) == count == sealed["trials"])
    check("sealed trial/provision hashes", service.sha(path) == sealed["sha256"]
          and service.sha(folder / f"{split}-provision.json") == sealed["provision_sha256"])
    check("sealed evidence hashes", set(sealed["evidence_sha256"]) == {
        f"{split}-schedule.json", f"{split}-server.json",
        f"{split}-server-log-lines.txt", f"{split}-cleanup.json"}
        and all(service.sha(folder / name) == expected
                for name, expected in sealed["evidence_sha256"].items()))
    check("owned runtime absent", cleanup["absence_proved"] and not cleanup["containers_readback"]
          and not cleanup["networks_readback"] and not cleanup["art_a_p1_networks"])
    check("run identity", server["run_id"] == public["run_id"] == sealed["run_id"])
    controls = public["network_positive_controls"]
    immutable = configuration == "immutable"
    required_controls = {
        ("bob", "live peer listener on its own network"),
        ("alice", "same live listener from the other network")}
    for actor in ("alice", "bob"):
        required_controls.add((actor, "assigned serving route" if immutable else "assigned gateway route"))
        required_controls.add((actor, "peer serving route" if immutable else "direct backend bypass"))
    if not immutable:
        required_controls.add(("gateway", "backend is reachable from the gateway"))
    check("network positive controls and listener stop", controls["passed"] and controls["listener_stopped"]
          and controls["listener_exit_code"] == 0 and controls["checks"] == len(controls["records"])
          and len(controls["records"]) == len(required_controls)
          and {(row["actor"], row["name"]) for row in controls["records"]} == required_controls
          and all(row["connected"] == row["expected_connected"] for row in controls["records"]))
    boundary = public["boundary_readback"]
    required_containers = {"alice", "bob", "mirror_alice", "mirror_bob"} if immutable \
        else {"alice", "bob", "gateway", "upstream"}
    required_networks = {"alice", "bob"} if immutable else {"alice", "bob", "services"}
    if configuration == "sham":
        required_containers.update(("dummy_alice", "dummy_bob"))
        required_networks.update(("dummy_alice", "dummy_bob"))
    check("complete boundary inventory", set(boundary["containers"]) == required_containers
          and set(boundary["networks"]) == required_networks)
    if not required_containers.issubset(boundary["containers"]) \
            or not required_networks.issubset(boundary["networks"]):
        return {"configuration": configuration, "split": split, "trials": len(trials),
                "checks": checks, "passed": False, "failure_count": len(failures),
                "failures": failures[:30]}
    check("retained container and network boundaries",
          all(row["user"] == "20001:20001" and row["root_read_only"]
              and row["memory"] == row["memory_swap"] == 134217728
              and row["nano_cpus"] == 500000000 and row["pids_limit"] == 48
              and "ALL" in row["cap_drop"] and "no-new-privileges:true" in row["security_options"]
              and not row["forbidden_environment_present"] and row["mounts_read_only"]
              and not any(row["published_ports"].values())
              for row in boundary["containers"].values())
          and all(row["internal"] and row["options"].get(
              "com.docker.network.bridge.gateway_mode_ipv4") == "isolated"
              for row in boundary["networks"].values()))
    for actor in ("alice", "bob"):
        client = boundary["containers"].get(actor, {})
        own_networks = {boundary["networks"][actor]["name"]}
        if configuration == "sham":
            own_networks.add(boundary["networks"][f"dummy_{actor}"]["name"])
        check(f"{actor} private client routes", set(client.get("attached_networks", [])) == own_networks
              and set(client.get("network_addresses", {})) == own_networks
              and all(client["network_addresses"].values())
              and not client.get("bind_mount_destinations", []))

    def actor_ip(actor, dummy_route=False):
        role = f"dummy_{actor}" if dummy_route else actor
        network_name = boundary["networks"][role]["name"]
        return boundary["containers"].get(actor, {}).get("network_addresses", {}).get(network_name)
    expected = plan.schedule(configuration, split, count)
    check("retained exact schedule", json.loads((folder / f"{split}-schedule.json").read_text()) == expected)
    check("balanced labels", Counter(row["label"] for row in trials) == {0: count // 2, 1: count // 2})
    gateway, backend, router, dummy, mirror, wire = [defaultdict(list) for _ in range(6)]
    if immutable:
        check("frozen catalog and private queues", public["private_queue_size"] == 5
              and server["upstream_requests"] == 0)
        for actor, data in server["tenants"].items():
            for row in data["response_log"]:
                mirror[row["marker"]].append(row)
            for row in data["wire_log"]:
                wire[row["marker"]].append(row)
            check(f"{actor} startup", any(row.get("ready") and row.get("edition") == 2
                  and row["manifest_sha256"] == public["manifest_sha256"]
                  and row["queue_size"] == 5 for row in data["startup"]))
    else:
        check("separate namespace/storage policy", public["configuration"] == "separate"
              and public["storage_blocked"] and public["negative_cache_period_seconds"] == 1800
              and len(set(public["repositories"].values())) == 2)
        for row in server["gateway_log"]:
            if row.get("marker"):
                gateway[row["marker"]].append(row)
        for line in (folder / f"{split}-server-log-lines.txt").read_text().splitlines():
            if line.startswith("{"):
                row = json.loads(line)
                router[row.get("request_User-Agent")].append(row)
            else:
                fields = line.split("|")
                if len(fields) >= 11:
                    backend[fields[10]].append({"principal": fields[3], "trace_id": fields[1], "method": fields[4],
                                               "path": fields[5], "status": int(fields[6]),
                                               "start_utc_ns": utc_ns(fields[0])})
        for actor, records in server.get("dummy_logs", {}).items():
            for row in records:
                if row.get("marker"):
                    dummy[row["marker"]].append(row)
    all_requests, expected_markers = [], set()
    for trial, planned in zip(trials, expected):
        tid = trial["trial_id"]
        check(f"{tid} exact assignment", all(trial.get(key) == value for key, value in planned.items()))
        check(f"{tid} no suppressed failures", not trial["batch_must_stop"]
              and not trial["failed_request_markers"] and trial["pre"] == [])
        check(f"{tid} fixed controller and dispatch records",
              trial["scheduled_sender_jobs"] == 128
              and trial["controller_start_utc_ns"] <= trial["epoch_utc_ns"] <= trial["controller_end_utc_ns"]
              and trial["receiver_schedule_lead_ms"] == min(
                  (trial["epoch_utc_ns"] - row["schedule_setup_utc_ns"]) / 1e6
                  for row in trial["post"]))
        for actor, observed, generated in [
            (trial["receiver"], trial["post"], plan.receiver_jobs(public, public["run_id"], trial)),
            (trial["sender"], trial["sender_records"],
             plan.sender_jobs(public, public["run_id"], trial, configuration))]:
            check(f"{tid} fixed request count", len(observed) == len(generated))
            for row, job in zip(observed, generated):
                check(f"{tid} request grammar {job['marker']}",
                      all(row.get(key) == job[key] for key in ["method", "path", "marker", "probe"])
                      and row["actor"] == actor
                      and lower_headers(row["request_headers"]) == lower_headers(job["headers"])
                      and row["scheduled_offset_s"] == job["offset_s"]
                      and row["schedule_epoch_utc_ns"] == trial["epoch_utc_ns"])
                check(f"{tid} observed local timing {job['marker']}",
                      row["start_lag_ms"] >= 0 and row["elapsed_ms"] >= 0
                      and trial["controller_start_utc_ns"] <= row["schedule_setup_utc_ns"]
                      <= row["start_utc_ns"] <= row["end_utc_ns"] <= trial["controller_end_utc_ns"]
                      and row["end_utc_ns"] >= row["start_utc_ns"]
                      and row["start_utc_ns"] >= trial["epoch_utc_ns"]
                      + int(job["offset_s"] * 1e9) - 2_000_000)
                all_requests.append(row)
    # Profile captures are setup requests, retained outside trial observations.
    if configuration == "sham":
        all_requests.extend(row for profile in public["sham_profiles"].values()
                            for row in profile["captured_requests"])
    forwarded_markers, gateway_markers, dummy_markers = set(), set(), set()
    for request in all_requests:
        marker = request["marker"]
        check(f"unique client marker {marker}", marker not in expected_markers)
        expected_markers.add(marker)
        check(f"valid request result {marker}", request["status"] is not None and request["status"] < 500)
        if request.get("body_text") is not None:
            check(f"retained body bytes {marker}", hashlib.sha256(
                request["body_text"].encode()).hexdigest() == request["body_sha256"])
        full = request["path"] if immutable else "/artifactory" + request["path"]
        wanted_headers = lower_headers(request["request_headers"])
        if request.get("target") == "dummy":
            dummy_markers.add(marker)
            check(f"one isolated sham request {marker}", configuration == "sham"
                  and len(dummy[marker]) == 1 and not gateway.get(marker)
                  and not backend.get(marker) and not router.get(marker))
            if len(dummy[marker]) == 1:
                event = dummy[marker][0]
                check(f"sham method/target/headers/status {marker}",
                      event["method"] == request["method"] and event["path"] == full
                      and event["client_ip"] == actor_ip(request["actor"], dummy_route=True)
                      and event["status"] == request["status"]
                      and event["request_headers"] == wanted_headers)
            continue
        gateway_markers.add(marker)
        if immutable:
            check(f"one private service response and wire request {marker}",
                  len(mirror[marker]) == len(wire[marker]) == 1)
            if len(mirror[marker]) != 1 or len(wire[marker]) != 1:
                continue
            response, event = mirror[marker][0], wire[marker][0]
            check(f"private origin and response {marker}",
                  event["origin_permitted"] and event["principal"] == public["users"][request["actor"]]
                  and event["client_ip"] == actor_ip(request["actor"])
                  and event["method"] == response["method"] == request["method"]
                  and event["path"] == response["path"] == full
                  and event["status"] == response["status"] == request["status"]
                  and event["request_headers"] == wanted_headers
                  and response["body_bytes"] == request["body_length"]
                  and response["body_sha256"] == request["body_sha256"]
                  and response["upstream_requests"] == 0)
            continue
        check(f"one gateway row {marker}", len(gateway[marker]) == 1)
        if len(gateway[marker]) != 1:
            continue
        event = gateway[marker][0]
        check(f"gateway origin/target/headers/response {marker}",
              event["gateway_edition"] == 2 and event["principal"] == public["users"][request["actor"]]
              and event["client_ip"] == actor_ip(request["actor"])
              and event["method"] == request["method"] and event["path"] == full
              and event["status"] == request["status"]
              and lower_headers(event["request_headers"]) == wanted_headers
              and event["body_bytes"] == request["body_length"]
              and event["body_sha256"] == request["body_sha256"])
        if event["policy_reason"]:
            check(f"policy block has no backend {marker}", event["policy_reason"] == "storage"
                  and event["status"] == 403 and not backend.get(marker) and not router.get(marker))
            continue
        forwarded_markers.add(marker)
        check(f"one backend and router request {marker}", len(backend[marker]) == len(router[marker]) == 1)
        if len(backend[marker]) != 1 or len(router[marker]) != 1:
            continue
        art, route = backend[marker][0], router[marker][0]
        backend_path = request["path"].partition("?")[0]
        directory = request["method"] == "GET" and any(backend_path == f"/{repo}-cache/"
                      for repo in public["repositories"].values()) and art["path"] == "/api/nativeBrowser"
        check(f"backend identity/method/status/full target {marker}",
              art["principal"] == event["principal"] and art["method"] == request["method"]
              and art["status"] == request["status"] and (art["path"] == backend_path or directory)
              and route["RequestPath"] == full and route["RequestMethod"] == request["method"]
              and route["DownstreamStatus"] == request["status"])
        check(f"backend/router trace identity {marker}",
              bool(art["trace_id"]) and art["trace_id"] == route.get("TraceId"))
        duration = route.get("Duration")
        start = utc_ns(route["StartUTC"])
        check(f"complete router interval inside client interval {marker}",
              isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0
              and request["start_utc_ns"] - 2_000_000 <= start <= start + duration
              <= request["end_utc_ns"] + 2_000_000)
    if immutable:
        check("complete private service coverage", set(mirror) == set(wire) == gateway_markers)
    else:
        check("complete gateway coverage", set(gateway) == gateway_markers)
        check("complete backend coverage", set(backend) == set(router) == forwarded_markers)
        check("complete sham coverage", set(dummy) == dummy_markers)
    return {"configuration": configuration, "split": split, "trials": len(trials),
            "checks": checks, "passed": not failures, "failures": failures[:30],
            "failure_count": len(failures), "client_requests": len(all_requests)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("split", choices=("calibration", "holdout"))
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    service.check_sources()
    if args.smoke and args.data.name != "smoke-p2-s1":
        raise RuntimeError("Smoke bypass accepts only its named implementation dataset")
    if args.split == "holdout":
        from collect import verify_model_freeze
        verify_model_freeze(args.data)
    results = [audit_split(args.data / name, name, args.split, args.smoke)
               for name in plan.CONFIGURATIONS]
    output = {"passed": all(row["passed"] for row in results), "runs": results,
              "smoke": args.smoke, "source_sha256": service.sha(Path(__file__))}
    (args.data / f"{args.split}-audit-results.json").write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
