#!/usr/bin/env python3
"""Check retained P1 schedules and client/gateway/backend joins.

Main holdout access requires the final freeze. Smoke audit is explicit and
does not supply a main result. Missing or duplicate marked records fail.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import meter

HERE = Path(__file__).resolve().parent
TOLERANCE_NS = 2_000_000


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def time_ns(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9)


def backend_matches(method, full_target, backend_path, repos):
    path = full_target.removeprefix("/artifactory").partition("?")[0]
    if backend_path == path:
        return True
    roots = {f"/{repo}/" for repo in repos} | {f"/{repo}-cache/" for repo in repos}
    return method == "GET" and path in roots and backend_path == "/api/nativeBrowser"


def check_freeze():
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise SystemExit(f"Frozen file changed: {name}")


def audit_run(folder, run, smoke):
    failures, checks = [], 0

    def check(name, condition):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)

    provision = json.loads((folder / "provision.json").read_text())
    server = json.loads((folder / "server.json").read_text())
    seal = json.loads((folder / "sealed.json").read_text())
    cleanup = json.loads((folder / "cleanup.json").read_text())
    n_cal, n_hold = (10, 10) if smoke else (300, 1000)
    trials = []
    for split, count in [("calibration", n_cal), ("holdout", n_hold)]:
        path = folder / f"{split}.jsonl"
        check(f"{split} sealed hash", sha(path) == seal["files"][split]["sha256"])
        data = rows(path)
        check(f"{split} count", len(data) == count == seal["files"][split]["trials"])
        check(f"{split} balanced", Counter(row["label"] for row in data) == {0: count // 2, 1: count // 2})
        trials.extend(data)
    expected_schedule = meter.schedule(run, n_cal, n_hold)
    check("schedule length", len(trials) == len(expected_schedule))
    check("run identifiers", server["run_id"] == provision["run_id"] == seal["run_id"])
    check("cleanup", cleanup["absence_proved"] and not cleanup["containers_readback"]
          and not cleanup["networks_readback"] and not cleanup["art_a_p1_networks"])
    check("configuration", provision["configuration"] == ("open" if run in ["aa", "sham"] else run))
    check("storage policy", provision["storage_blocked"] == (run in ["combined", "separate"]))
    check("miss period", provision["negative_cache_period_seconds"] == (0 if run == "combined" else 1800))
    gw, backend, router = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in server["gateway_log"]:
        gw[row["marker"]].append(row)
    for line in (folder / "server-log-lines.txt").read_text().splitlines():
        if line.startswith("{"):
            value = json.loads(line)
            router[value.get("request_User-Agent")].append(value)
        else:
            fields = line.split("|")
            if len(fields) >= 11:
                backend[fields[10]].append({"identity": fields[3], "method": fields[4],
                                            "path": fields[5], "status": int(fields[6]),
                                            "start_ns": time_ns(fields[0])})
    expected_markers, invalid_trials, forwarded, blocked, dummy = set(), set(), 0, 0, 0
    users, repos = provision["users"], list(provision["repositories"].values())
    # The public provision omits the prefetch ETag. It is unused for generating
    # the method/path/marker contract audited here. Outbound request headers
    # are not independently attested by these retained gateway log fields.
    generation_state = {**provision, "fixture_etag": None}
    for trial, expected in zip(trials, expected_schedule):
        tid = trial["trial_id"]
        before_failures = len(failures)
        check(f"{tid} schedule", all(trial.get(key) == value for key, value in expected.items()))
        for part, generated in [("pre", meter.pre_battery(provision, provision["run_id"], trial)),
                                ("post", meter.post_battery(provision, provision["run_id"], trial))]:
            observed = trial[part]
            check(f"{tid} {part} battery", len(observed) == len(generated)
                  and all(all(row.get(key) == request[key] for key in ["probe", "method", "path", "marker"])
                          for row, request in zip(observed, generated)))
            check(f"{tid} {part} receiver identity", all(row["actor"] == trial["receiver"] for row in observed))
        check(f"{tid} observation order", max(row["end_utc_ns"] for row in trial["pre"])
              <= min(row["start_utc_ns"] for row in trial["post"]))
        if trial["label"] == 0 or run == "aa":
            check(f"{tid} idle sender", not trial["sender_records"])
        else:
            check(f"{tid} active sender retained", bool(trial["sender_records"]))
            sent = trial["sender_records"]
            check(f"{tid} sender identity", all(row["actor"] == trial["sender"] for row in sent))
            is_load = trial["recipe"] == "load"
            check(f"{tid} sender recipe count", (len(sent) >= meter.LOAD_CONCURRENCY
                  and len(sent) % meter.LOAD_CONCURRENCY == 0) if is_load else len(sent) == 1)
            for index, row in enumerate(sent):
                burst = index // meter.LOAD_CONCURRENCY if is_load else 0
                generated = meter.sender_requests(generation_state, provision["run_id"], trial, burst)
                expected_request = generated[index % meter.LOAD_CONCURRENCY if is_load else 0]
                if run == "sham":
                    expected_request = {**expected_request, "path": "/sham" + expected_request["path"],
                                        "target": "dummy"}
                check(f"{tid} sender recipe {index}",
                      all(row.get(key) == expected_request[key] for key in ["method", "path", "marker"])
                      and row.get("target", "gateway") == expected_request.get("target", "gateway"))
        receiver_times = [row["start_utc_ns"] for row in trial["post"]]
        if trial["sender_records"]:
            check(f"{tid} sender precedes receiver",
                  max(row["end_utc_ns"] for row in trial["sender_records"]) <= min(receiver_times))
        for request in trial["pre"] + trial["post"] + trial["sender_records"]:
            marker = request["marker"]
            check(f"{tid} unique client marker {marker}", marker not in expected_markers)
            expected_markers.add(marker)
            check(f"{tid} nonnegative request duration", request["elapsed_ms"] >= 0
                  and request["end_utc_ns"] >= request["start_utc_ns"])
            if request.get("body_text") is not None:
                check(f"{tid} retained text body hash {marker}",
                      hashlib.sha256(request["body_text"].encode()).hexdigest() == request["body_sha256"])
            if request.get("target") == "dummy":
                dummy += 1
                check(f"{tid} dummy isolated from service ledger", run == "sham" and marker not in gw
                      and marker not in backend and marker not in router)
                continue
            check(f"{tid} one gateway row {marker}", len(gw[marker]) == 1)
            if len(gw[marker]) != 1:
                continue
            gateway = gw[marker][0]
            full = "/artifactory" + request["path"]
            check(f"{tid} gateway identity/response {marker}",
                  gateway["principal"] == users[request["actor"]]
                  and gateway["path"] == full and gateway["method"] == request["method"]
                  and gateway["status"] == request["status"])
            if gateway["policy_reason"] is not None:
                blocked += 1
                check(f"{tid} recognized policy block {marker}",
                      gateway["policy_reason"] == "storage" and gateway["status"] == 403
                      and request["path"].startswith("/api/storage/"))
                check(f"{tid} blocked request absent from backend {marker}", not backend[marker] and not router[marker])
            else:
                forwarded += 1
                check(f"{tid} one request/router row {marker}", len(backend[marker]) == len(router[marker]) == 1)
                if len(backend[marker]) != 1 or len(router[marker]) != 1:
                    continue
                art, route = backend[marker][0], router[marker][0]
                check(f"{tid} backend identity/response {marker}",
                      art["identity"] == gateway["principal"]
                      and art["method"] == gateway["method"] and art["status"] == gateway["status"]
                      and backend_matches(gateway["method"], full, art["path"], repos))
                check(f"{tid} full router target {marker}",
                      route["RequestPath"] == full and route["RequestMethod"] == gateway["method"]
                      and route["DownstreamStatus"] == gateway["status"])
                lower, upper = request["start_utc_ns"] - TOLERANCE_NS, request["end_utc_ns"] + TOLERANCE_NS
                check(f"{tid} backend inside client window {marker}", lower <= art["start_ns"] <= upper
                      and lower <= time_ns(route["StartUTC"]) <= upper)
        if len(failures) != before_failures:
            invalid_trials.add(tid)
    gateway_markers = {request["marker"] for trial in trials
                       for request in trial["pre"] + trial["post"] + trial["sender_records"]
                       if request.get("target") != "dummy"}
    check("gateway coverage", set(gw) == gateway_markers)
    check("backend coverage", {key for key, value in backend.items() if value}
          == {key for key, value in router.items() if value}
          == {marker for marker in gateway_markers if len(gw[marker]) == 1 and gw[marker][0]["policy_reason"] is None})
    return {"run": run, "checks": checks, "passed": not failures, "failures": failures[:30],
            "failure_count": len(failures), "invalid_trials": sorted(invalid_trials),
            "trials": len(trials), "forwarded": forwarded, "blocked": blocked, "dummy": dummy}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if not args.smoke:
        check_freeze()
    elif "smoke" not in args.data.name:
        raise SystemExit("Smoke bypass accepts only a smoke-named dataset directory")
    results = [audit_run(args.data / run, run, args.smoke) for run in meter.RUNS]
    output = {"passed": all(row["passed"] for row in results), "smoke": args.smoke,
              "audit_source_sha256": sha(Path(__file__)), "runs": results}
    (args.data / "audit-results.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
