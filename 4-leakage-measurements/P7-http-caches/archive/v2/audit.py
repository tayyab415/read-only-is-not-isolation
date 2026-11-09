"""Independent P7 identity, cache-cause, request-count, and cleanup checks."""
import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import plan
import configuration

HERE = Path(__file__).resolve().parent
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
MISSING_SHA = hashlib.sha256(b"Missing harmless fixture\n").hexdigest()
MISSING_BYTES = len(b"Missing harmless fixture\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def native(product, text):
    if text is None:
        raise ValueError("Missing native access log")
    if product == "nginx":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    output = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 8:
            raise ValueError("Squid's native access format changed")
        epoch, address, method, target, status, cache, duration, marker = fields
        output.append({"end_epoch_s": epoch, "client_ip": address, "method": method,
            "path": target, "status": int(status), "cache_status": cache,
            "request_time_s": str(Decimal(duration) / 1000), "marker": marker})
    return output


def grouped(records):
    output = defaultdict(list)
    for row in records:
        if row.get("marker"):
            output[row["marker"]].append(row)
    return output


def audit_cohort(folder):
    seal = json.loads((folder / "sealed.json").read_text())
    failures, checks = [], 0

    def check(name, condition):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)

    if not seal.get("complete"):
        return {"passed": False, "checks": 1, "scheduled": seal["scheduled"],
                "completed": seal["completed"], "failure_count": 1, "failures": ["Incomplete P7 cohort."]}
    check("exact retained file hashes", all(sha(folder / name) == digest for name, digest in seal["sha256"].items())
        and {"schedule.json", "trials.jsonl", "requests.jsonl", "request-reservations.jsonl",
             "provision.json", "server.json", "cache-stop.json", "cleanup.json"} <= set(seal["sha256"]))
    public = json.loads((folder / "provision.json").read_text())
    server = json.loads((folder / "server.json").read_text())
    cleanup = json.loads((folder / "cleanup.json").read_text())
    stopped = json.loads((folder / "cache-stop.json").read_text())
    trials, requests = rows(folder / "trials.jsonl"), rows(folder / "requests.jsonl")
    planned = plan.schedule(seal["product"], seal["delay_ms"], seal["arm"], seal["split"])
    check("complete exact scheduled cohort", len(trials) == seal["scheduled"] == seal["completed"] == len(planned)
        and json.loads((folder / "schedule.json").read_text()) == planned)
    check("run and source identity", public["run_id"] == server["run_id"] == cleanup["run_id"] == seal["run_id"]
        and seal["source_freeze_sha256"] == sha(HERE / "SOURCES-FROZEN.json")
        and public["build"]["source_freeze_sha256"] == seal["source_freeze_sha256"])
    if seal["split"] == "holdout":
        check("main decoder freeze", seal["model_freeze_sha256"] == sha(HERE / "FROZEN.json"))
    check("process stop and cleanup", stopped["passed"] and seal["cleanup_absence_proved"]
        and cleanup["absence_proved"] and not cleanup["containers_readback"] and not cleanup["networks_readback"]
        and cleanup["artifactory_touched"] is False
        and len(stopped["records"]) == len(set(public["caches"].values()))
        and all(row["signal_exit_code"] == row["process_exit_code"] == 0 for row in stopped["records"]))
    check("actual version and negative-cache feature", "nginx/1.30.6" in public["build"]["binaries"]["nginx"]["version"]
        and "Squid Cache: Version 7.8" in public["build"]["binaries"]["squid"]["version"]
        and "--enable-http-violations" in public["build"]["binaries"]["squid"]["version"]
        and all(row["exit_code"] == 0 for row in public["build"]["binaries"].values()))
    boundary = public["boundary"]
    expected_names = {*public["clients"].values(), public["origin"], *public["caches"].values()}
    check("exact runtime inventory", set(boundary["containers"]) == expected_names
        and set(boundary["networks"]) == set(public["networks"].values())
        and set(server["caches"]) == set(public["caches"].values())
        and set(public["configurations"]) == set(public["caches"].values()))
    for name, row in boundary["containers"].items():
        is_cache = name in public["caches"].values()
        actor = next((actor for actor, selected in public["clients"].items() if selected == name), None)
        attached = {public["networks"][actor]} if actor else {public["networks"]["services"]} if name == public["origin"] else \
            {public["networks"][who] for who, cache in public["caches"].items() if cache == name} | {public["networks"]["services"]}
        memory = 268435456 if is_cache and seal["product"] == "squid" else 134217728
        pids = 96 if is_cache and seal["product"] == "squid" else 48
        check(f"{name} retained confinement", row["user"] == "20001:20001" and row["root_read_only"]
            and row["memory"] == row["memory_swap"] == memory and row["nano_cpus"] == 500000000
            and row["pids_limit"] == pids and "ALL" in row["cap_drop"]
            and "no-new-privileges:true" in row["security_options"] and not row["forbidden_environment_present"]
            and not any(row["published_ports"].values()) and row["mounts_read_only"]
            and set(row["network_addresses"]) == attached and all(row["network_addresses"].values())
            and (not is_cache or row["image_id"] == public["build"]["image_id"])
            and (not actor or not row["bind_mount_destinations"]))
    check("private internal networks", all(row["internal"] and row["options"].get(
        "com.docker.network.bridge.gateway_mode_ipv4") == "isolated" for row in boundary["networks"].values()))
    controls = public["network_controls"]
    check("complete live route controls", controls["passed"] and controls["listener_stopped"]
        and controls["listener_exit_code"] == 0 and len(controls["records"]) == 6
        and {(row["actor"], row["name"]) for row in controls["records"]} ==
            {(actor, name) for actor in ("alice", "bob") for name in ("assigned cache", "direct origin", "live peer listener")}
        and all(row["connected"] == row["expected_connected"] for row in controls["records"]))
    ips = {actor: boundary["containers"][name]["network_addresses"][public["networks"][actor]]
           for actor, name in public["clients"].items()}
    source_ip = boundary["containers"][public["origin"]]["network_addresses"][public["networks"]["services"]]
    for name, configured in public["configurations"].items():
        origins = {ips[actor]: actor for actor, cache in public["caches"].items() if cache == name}
        expected_text = getattr(configuration, seal["product"])(public["effective_arm"], origins, public["origin"])
        check(f"{name} configuration bound to observed identities", configured["origins"] == origins
            and configured["configuration"] == expected_text
            and configured["configuration_sha256"] == hashlib.sha256(expected_text.encode()).hexdigest()
            and configured["parse_exit_code"] == 0)
    access, source = defaultdict(list), grouped(server["origin_log"])
    helper = defaultdict(list)
    for name, cache in server["caches"].items():
        for event in native(seal["product"], cache["access_log"]):
            event["cache_container"] = name
            access[event["marker"]].append(event)
        if seal["product"] == "squid" and seal["arm"] == "tenant-keyed":
            if cache["store_id_log"] is None:
                check("key helper evidence exists", False)
            else:
                for line in cache["store_id_log"].splitlines():
                    event = json.loads(line)
                    check("helper never fails or falls back", "failure_type" not in event)
                    if "failure_type" not in event:
                        helper[(event["client_ip"], event["url"])].append(event)
    reservations = rows(folder / "request-reservations.jsonl")
    check("request ledger checkpoint and caps", sha(folder / "request-reservations.jsonl") == seal["request_ledger_sha256_at_seal"]
        and len(reservations) <= 1200 and sum(row["reserved_http_requests"] for row in reservations) <= 2400
        and len({row["marker"] for row in reservations}) == len(reservations))
    reserved = grouped(reservations)
    http_requests = [row for row in requests if row["execute"] == 1]
    check("unique dispatch markers", len({row["marker"] for row in requests}) == len(requests))
    observed = grouped(requests)
    expected_dispatches = {row["marker"] for row in public["canaries"]["records"]}
    expected_source, expected_access = set(), set()
    canaries = public["canaries"]
    check("all live canaries retained", canaries["passed"] and len(canaries["records"]) == 9
        and canaries["negative_lookup_counts"] == canaries["expected_lookup_counts"]
        == ([1, 1] if seal["arm"] == "disabled" else [1, 0])
        and canaries["records"][-1]["actor"] == "trusted-source-control"
        and canaries["records"][-1]["status"] == 403)
    check("canary reuse independently matches origin ledger",
        [len(source[canaries["records"][i]["marker"]]) for i in range(2)] == canaries["expected_lookup_counts"])
    for trial, expected in zip(trials, planned):
        tid = trial["trial_id"]
        check(f"{tid} exact assignment", all(trial.get(key) == value for key, value in expected.items()))
        dispatch, receiver = trial["sender_dispatch"], trial["receiver_request"]
        sender = trial["sender_request"]
        check(f"{tid} equal private sender grammar", dispatch["actor"] == trial["sender"]
            and dispatch["execute"] == int(trial["active"]) and dispatch["method"] == "GET"
            and dispatch["path"] == trial["path"] and dispatch["headers"] == {}
            and dispatch["marker"] == plan.marker(seal["run_id"], trial, trial["sender"], "sender")
            and (sender == dispatch and sender["status"] == 404 and sender["body_sha256"] == MISSING_SHA
                 and sender["body_bytes"] == MISSING_BYTES if trial["active"]
                 else sender is None and dispatch.get("idle") and dispatch["status"] is None))
        check(f"{tid} receiver assignment and fixed bytes", receiver["actor"] == trial["receiver"]
            and receiver["execute"] == 1 and receiver["method"] == "GET"
            and receiver["path"] == trial["path"] and receiver["headers"] == {}
            and receiver["marker"] == plan.marker(seal["run_id"], trial, trial["receiver"], "receiver")
            and receiver["status"] == 404 and receiver["body_sha256"] == MISSING_SHA
            and receiver["body_bytes"] == MISSING_BYTES)
        check(f"{tid} dispatch and receiver order", trial["controller_start_utc_ns"] <= dispatch["start_utc_ns"]
            <= dispatch["end_utc_ns"] == trial["sender_dispatch_end_utc_ns"]
            <= receiver["start_utc_ns"] <= receiver["end_utc_ns"] <= trial["controller_end_utc_ns"])
        expected_dispatches.update((dispatch["marker"], receiver["marker"]))
        shared_hit = seal["arm"] == "shared" and trial["active"]
        check(f"{tid} sender origin fill", len(source[dispatch["marker"]]) == (1 if trial["active"] else 0))
        check(f"{tid} receiver application-cache cause", len(source[receiver["marker"]]) == (0 if shared_hit else 1))
        if sender:
            expected_source.add(sender["marker"])
        if not shared_hit:
            expected_source.add(receiver["marker"])
        if len(access[receiver["marker"]]) == 1:
            cache_status = access[receiver["marker"]][0]["cache_status"]
            check(f"{tid} native receiver hit agrees with cause",
                  (cache_status == "HIT" if seal["product"] == "nginx" else cache_status == "TCP_NEGATIVE_HIT") == shared_hit)
    for request in requests:
        marker, actor = request["marker"], request["actor"]
        check(f"{marker} dispatch evidence retained", len(observed[marker]) == 1
            and request["end_utc_ns"] >= request["start_utc_ns"] and request["elapsed_ms"] >= 0
            and abs((request["end_utc_ns"] - request["start_utc_ns"]) / 1000000 - request["elapsed_ms"]) <= 2)
        if request["execute"] == 0:
            check(f"{marker} idle has no cache/origin request or reservation", not access[marker]
                and not source[marker] and not reserved[marker])
            continue
        expected_access.add(marker)
        check(f"{marker} counted before dispatch", len(reserved[marker]) == 1
            and reserved[marker][0]["reserved_http_requests"] == 2
            and reserved[marker][0]["utc_ns"] <= request["start_utc_ns"])
        check(f"{marker} one native cache request", len(access[marker]) == 1)
        if len(access[marker]) != 1:
            continue
        event = access[marker][0]
        unknown = actor == "trusted-source-control"
        cache_name = public["caches"]["alice" if unknown else actor]
        address = source_ip if unknown else ips[actor]
        check(f"{marker} native origin/method/full target/status", event["cache_container"] == cache_name
            and event["client_ip"] == address and event["method"] == request["method"]
            and event["path"] == request["target"] and event["status"] == request["status"])
        check(f"{marker} native request inside client interval", request["start_utc_ns"] - 2000000
            <= int(Decimal(str(event["end_epoch_s"])) * 1000000000) <= request["end_utc_ns"] + 2000000)
        if seal["product"] == "nginx":
            check(f"{marker} trusted key identity", event["actor"] == ("" if unknown else actor)
                and event["body_bytes"] == request["body_bytes"])
        elif seal["arm"] == "tenant-keyed" and not unknown:
            events = helper[(address, request["target"])]
            wanted = "http://p7-key.invalid/" + actor + "/" + hashlib.sha256(request["target"].encode()).hexdigest()
            check(f"{marker} trusted StoreID mapping exists", bool(events)
                and all(row["actor"] == actor and row["store_id"] == wanted for row in events))
        check(f"{marker} no suppressed request failure", request["status"] is not None and request["status"] < 500)
        if unknown:
            check(f"{marker} rejected without origin work", not source[marker])
        check(f"{marker} at most one native origin request", len(source[marker]) <= 1)
        for origin in source[marker]:
            expected_source.add(marker)
            cache_ip = boundary["containers"][cache_name]["network_addresses"][public["networks"]["services"]]
            # NGINX converts a HEAD miss to a GET. This is a declared canary
            # rewrite, not a silent equality assumption.
            expected_methods = {"GET", "HEAD"} if request["method"] == "HEAD" and seal["product"] == "nginx" \
                else {request["method"]}
            check(f"{marker} origin route and interval", origin["client_ip"] == cache_ip
                and origin["method"] in expected_methods and origin["path"] == request["path"]
                and request["start_utc_ns"] - 2000000 <= origin["start_utc_ns"]
                <= origin["end_utc_ns"] <= request["end_utc_ns"] + 2000000)
            missing = "/missing/" in request["path"]
            body = b"Missing harmless fixture\n" if missing else b"J controls harmless artifact fixture v1\n"
            delivered = body if origin["method"] == "GET" else b""
            check(f"{marker} exact origin status/body/delay", origin["status"] == (404 if missing else 200)
                and origin["body_sha256"] == hashlib.sha256(delivered).hexdigest()
                and origin["body_bytes"] == len(delivered)
                and origin["delay_ms"] == (seal["delay_ms"] if missing else 0))
    check("complete dispatch/access/origin coverage", set(observed) == expected_dispatches
        and {key for key, values in access.items() if values} == expected_access
        and {key for key, values in source.items() if values} == expected_source)
    actual_http = len(http_requests) + sum(len(values) for values in source.values())
    check("actual HTTP count within reserved upper bound",
          actual_http <= 2 * len(http_requests) and len(http_requests) <= 1200 and actual_http <= 2400)
    return {"run_id": seal["run_id"], "product": seal["product"], "delay_ms": seal["delay_ms"],
            "arm": seal["arm"], "split": seal["split"], "passed": not failures, "checks": checks,
            "scheduled": len(planned), "completed": len(trials), "failure_count": len(failures),
            "failures": failures[:40], "controller_http_requests": len(http_requests),
            "origin_http_requests": sum(len(values) for values in source.values()), "total_http_requests": actual_http,
            "sealed_sha256": sha(folder / "sealed.json")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    args = parser.parse_args()
    freeze = json.loads((HERE / "SOURCES-FROZEN.json").read_text())
    for name, expected in freeze["sha256"].items():
        if sha(HERE / name) != expected:
            raise SystemExit("Frozen P7 audit source changed")
    results = [audit_cohort(path) for path in sorted(args.data.iterdir()) if path.is_dir()]
    expected = 4 if args.data.name == "g1-cal" else 20 if args.data.name == "g1-main" else 0
    output = {"passed": expected > 0 and len(results) == expected and all(row["passed"] for row in results),
              "cohorts": results, "audit_source_sha256": sha(Path(__file__)),
              "global_controller_http_requests": sum(row.get("controller_http_requests", 0) for row in results),
              "global_total_http_requests": sum(row.get("total_http_requests", 0) for row in results)}
    (args.data / "audit-results.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
