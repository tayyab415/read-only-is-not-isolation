"""Independent P8 inventory, boundary, utility and native-counter audit."""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path

from experiment import CONFIGURATIONS, FILE_BYTES, OFFSETS, PAYLOAD, counters, cpu_feature, digest
from experiment import executing, masked, parse_cpu, schedule
from runtime import validate_boundary

HERE = Path(__file__).resolve().parent


class Checks:
    def __init__(self):
        self.count, self.failures = 0, []

    def check(self, name, condition):
        self.count += 1
        if not condition:
            self.failures.append(name)

    def result(self):
        return {"passed": not self.failures, "checks": self.count,
                "failure_count": len(self.failures), "failures": self.failures[:60]}


def accounting_inventory(inventory, check, prefix):
    check(prefix + " cgroup v2", inventory["cgroup_v2"] is True)
    fields = (inventory["cpu_max"] or "").split()
    check(prefix + " effective CPU quota", len(fields) == 2 and fields[0] != "max"
          and int(fields[0]) * 2 == int(fields[1]))
    check(prefix + " effective memory", inventory["memory_max"] == str(128 * 1024 ** 2) + "\n")
    check(prefix + " namespace-root membership", inventory["self_cgroup"] == "0::/\n")
    mounts = [line.split() for line in inventory["mountinfo"].splitlines()]
    cgroup_mounts = [row for row in mounts if len(row) >= 10 and row[4] == "/sys/fs/cgroup"]
    check(prefix + " actual namespace-root cgroup2 mount",
          len(cgroup_mounts) == 1 and cgroup_mounts[0][3] == "/" and
          cgroup_mounts[0][cgroup_mounts[0].index("-") + 1] == "cgroup2")
    check(prefix + " usage available", "usage_usec" in inventory["cgroup"]["values"])


def sample_checks(samples, epoch, check, prefix, with_cgroup):
    check(prefix + " sample denominator", len(samples) == len(OFFSETS))
    check(prefix + " fixed sampling offsets", [row["scheduled_offset_s"] for row in samples] == list(OFFSETS))
    for index, row in enumerate(samples):
        tag = f"{prefix} sample {index}"
        check(tag + " common epoch", row["schedule_epoch_utc_ns"] == epoch)
        check(tag + " retained read interval", row["read_start_utc_ns"] <= row["read_end_utc_ns"])
        check(tag + " lag recomputes", abs(row["start_lag_ms"] - max(0, (
            row["read_start_utc_ns"] - epoch - round(row["scheduled_offset_s"] * 1e9)) / 1e6)) < 1e-8)
        parse_cpu(row["cpu_line"])
        if with_cgroup:
            raw = dict((key, int(value)) for key, value in
                       (line.split() for line in row["cgroup"]["raw"].splitlines()))
            check(tag + " native cgroup record", raw == row["cgroup"]["values"] and "usage_usec" in raw)
    check(prefix + " ordered reads", all(before["read_end_utc_ns"] <= after["read_start_utc_ns"]
                                         for before, after in zip(samples, samples[1:])))


def compare_cpu_views(observer, host, cpu_count, clock_ticks):
    first, second = counters(parse_cpu(observer["cpu_line"]))[0], counters(parse_cpu(host["cpu_line"]))[0]
    # The two read intervals need not be simultaneous. Retain the full
    # tolerance instead of asserting byte equality from different instants.
    separation = max(abs(observer["read_start_utc_ns"] - host["read_end_utc_ns"]),
                     abs(observer["read_end_utc_ns"] - host["read_start_utc_ns"])) / 1e9
    tolerance = cpu_count * (4 + clock_ticks * separation)
    return {"passed": abs(first - second) <= tolerance,
            "total_difference": abs(first - second), "tolerance_ticks": tolerance}


def join_http(row, response, wire, public, actor, check, prefix):
    boundary = public["boundary"]["containers"][actor]
    ip = boundary["network_addresses"][public["networks"][actor]]
    check(prefix + " unique response/wire", len(response) == len(wire) == 1)
    if len(response) != 1 or len(wire) != 1:
        return
    body, transport = response[0], wire[0]
    check(prefix + " expected delivered body", row["status"] == body["status"] == transport["status"] == 200
          and row["body_length"] == body["body_bytes"] == len(PAYLOAD)
          and row["body_sha256"] == body["body_sha256"] == digest(PAYLOAD))
    for event in (body, transport):
        check(prefix + " exact method/path/marker", all(event[key] == row[key] for key in ("method", "path", "marker")))
        check(prefix + " full interval", row["start_utc_ns"] - 2_000_000 <= event["start_utc_ns"]
              <= event["end_utc_ns"] <= row["end_utc_ns"] + 2_000_000)
    check(prefix + " assigned route", row["method"] == "GET" and row["path"] == "/fixture/pkg-00.bin")
    check(prefix + " actual origin and principal", transport["client_ip"] == ip
          and transport["principal"] == public["users"][actor] and transport["origin_permitted"]
          and transport["identity_source"] == "trusted network origin")
    check(prefix + " request headers", transport["request_headers"] == row["request_headers"] == {})
    check(prefix + " response identity", body["tenant"] == actor and body["upstream_requests"] == 0)
    check(prefix + " no transport error", transport["error"] is None)


def audit_cell(data, configuration, split):
    folder = data / configuration
    checks = Checks()
    check = checks.check
    try:
        seal = json.loads((folder / f"{split}-sealed.json").read_text())
        check("sealed complete denominator", seal["complete"] is True and
              seal["trials"] == seal["retained_trials"] == len(schedule(configuration, split)))
        check("exact sealed evidence inventory", set(seal["evidence_sha256"]) == {
            f"{split}-{name}.json" for name in ("schedule", "provision", "readiness", "server", "cleanup")})
        for name, expected in seal["evidence_sha256"].items():
            check("evidence SHA " + name, digest((folder / name).read_bytes()) == expected)
        path = folder / f"{split}.jsonl"
        check("sealed observations SHA", digest(path.read_bytes()) == seal["sha256"])
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        jobs = json.loads((folder / f"{split}-schedule.json").read_text())
        check("exact prospective schedule", jobs == schedule(configuration, split))
        check("exact retained denominator", len(rows) == len(jobs))
        public = json.loads((folder / f"{split}-provision.json").read_text())
        readiness = json.loads((folder / f"{split}-readiness.json").read_text())
        server = json.loads((folder / f"{split}-server.json").read_text())
        cleanup = json.loads((folder / f"{split}-cleanup.json").read_text())
        check("source freeze", public["source_freeze_sha256"] == digest((HERE / "SOURCES-FROZEN.json").read_bytes()))
        validate_boundary(public["boundary"], public, configuration, Path(public["mask_path"]))
        check("final boundary matches", True)
        controls = public["network_controls"]
        expected_routes = Counter({"live peer listener on its own network": 1,
                                   "same live listener from the other network": 1,
                                   "assigned serving route": 2, "peer serving route": 2})
        check("live TCP controls and listener stop", controls["passed"] and controls["listener_stopped"]
              and controls["checks"] == 6 and controls["listener_exit_code"] == 0
              and Counter(row["name"] for row in controls["records"]) == expected_routes
              and all(row["connected"] == row["expected_connected"] for row in controls["records"]))
        check("owned cleanup readback", cleanup["absence_proved"] is True and
              not cleanup["containers_readback"] and not cleanup["networks_readback"]
              and not cleanup["art_a_p1_networks"] and seal["cleanup_absence_proved"] is True)
        host_inventory = public["host_inventory"]
        mask_bytes = base64.b64decode(public["mask_bytes_b64"], validate=True)
        check("mask exact SHA", digest(mask_bytes) == public["mask_sha256"])
        check("private startup utility", all(item["ready"] and item["file_task"]["successful"]
              and item["file_task"]["sha256"] == digest(FILE_BYTES) and item["file_task"]["bytes"] == 1024
              for item in readiness.values()))
        for actor in ("alice", "bob"):
            accounting_inventory(readiness[actor]["inventory"], check, actor)
            for kind in ("cgroup", "net", "pid", "mnt"):
                check(actor + " private " + kind + " namespace",
                      readiness[actor]["inventory"]["namespace_ids"][kind] !=
                      host_inventory["namespace_ids"][kind] and
                      readiness[actor]["inventory"]["namespace_ids"][kind] !=
                      readiness["bob" if actor == "alice" else "alice"]["inventory"]["namespace_ids"][kind])
        check("server inventory", server["kind"] == "immutable" and server["upstream_requests"] == 0
              and set(server["tenants"]) == {"alice", "bob"})
        responses, wires = {}, {}
        for actor in ("alice", "bob"):
            responses[actor] = server["tenants"][actor]["response_log"]
            wires[actor] = server["tenants"][actor]["wire_log"]
        used = set()
        mechanism = {"native_scope_matches": 0, "mask_samples": 0, "host_live_trials": 0,
                     "late_receiver_setups": 0, "late_samples_over_20ms": 0,
                     "sender_overruns_ms": [], "host_counter_tolerances": []}
        for index, (row, job) in enumerate(zip(rows, jobs)):
            tag = f"{configuration}/{split}/{index}"
            check(tag + " trusted assignment", all(row[key] == value for key, value in job.items()))
            check(tag + " cell identity", row["configuration"] == configuration)
            check(tag + " complete tasks", not row["batch_must_stop"] and not row["failures"])
            epoch = row["epoch_utc_ns"]
            expected_rpc = {"op": "trial", "epoch_utc_ns": epoch, "token": job["token"]}
            receiver, sender = row["receiver"], row["sender"]
            check(tag + " observer gets no ground truth", receiver["rpc"] == expected_rpc)
            flag = executing(configuration, job["label"])
            check(tag + " private sender RPC", sender["rpc"] == {**expected_rpc, "execute": flag})
            check(tag + " independent pre-epoch setup", row["controller_start_utc_ns"] <=
                  min(sender["setup_utc_ns"], receiver["setup_utc_ns"]))
            mechanism["late_receiver_setups"] += receiver["setup_utc_ns"] >= epoch
            load = sender["load"]
            check(tag + " fixed sender window", load["execute"] == flag and
                  load["planned_start_utc_ns"] == epoch + 200_000_000 and
                  load["planned_end_utc_ns"] == epoch + 2_200_000_000)
            check(tag + " sender accounting", load["process_cpu_ns"] >= 0 and
                  load["cgroup_after"]["values"]["usage_usec"] >= load["cgroup_before"]["values"]["usage_usec"]
                  and load["start_utc_ns"] <= load["end_utc_ns"])
            check(tag + " actual active/idle work", (load["iterations"] > 0 and load["process_cpu_ns"] > 0)
                  if flag else load["iterations"] == 0)
            mechanism["sender_overruns_ms"].append(max(0, (load["end_utc_ns"] -
                                                           load["planned_end_utc_ns"]) / 1e6))
            sample_checks(receiver["samples"], epoch, check, tag + " receiver", True)
            sample_checks(row["host_samples"], epoch, check, tag + " host", False)
            feature, host_feature = cpu_feature(receiver["samples"]), cpu_feature(row["host_samples"])
            check(tag + " host remains live", host_feature["live_counter"])
            mechanism["host_live_trials"] += host_feature["live_counter"]
            for sample, host_sample in zip(receiver["samples"], row["host_samples"]):
                mechanism["late_samples_over_20ms"] += sample["start_lag_ms"] > 20
                if masked(configuration):
                    check(tag + " actual masked counter bytes", sample["file_sha256"] == public["mask_sha256"]
                          and sample["cpu_line"] == mask_bytes.splitlines()[0].decode())
                    mechanism["mask_samples"] += 1
                else:
                    comparison = compare_cpu_views(sample, host_sample, host_inventory["cpu_count"],
                                                   host_inventory["clock_ticks"])
                    check(tag + " actual native host accounting", comparison["passed"])
                    mechanism["native_scope_matches"] += comparison["passed"]
                    mechanism["host_counter_tolerances"].append(comparison)
            if masked(configuration):
                check(tag + " actual static startup/after bytes",
                      readiness["bob"]["inventory"]["cpu_stat"]["file_sha256"] == public["mask_sha256"]
                      and receiver["view_after"]["file_sha256"] == public["mask_sha256"])
                check(tag + " removed live counter input", not feature["live_counter"] and feature["value"] == 0)
            else:
                check(tag + " observer native counters remain live", feature["live_counter"])
            for actor, participant in (("alice", sender), ("bob", receiver)):
                check(tag + " " + actor + " file utility", participant["file"]["successful"] and
                      participant["file"]["sha256"] == digest(FILE_BYTES) and participant["file"]["bytes"] == 1024)
                check(tag + " " + actor + " fixed HTTP denominator", len(participant["http"]) == 2)
                for phase, request in zip(("pre", "post"), participant["http"]):
                    marker = f"p8/{public['run_id']}/{job['token']}/{actor}/{phase}"
                    check(tag + " " + actor + " unique marker", marker == request["marker"] and marker not in used)
                    used.add(marker)
                    match_response = [item for item in responses[actor] if item["marker"] == marker]
                    match_wire = [item for item in wires[actor] if item["marker"] == marker]
                    join_http(request, match_response, match_wire, public, actor, check, tag + "/" + actor + "/" + phase)
                    check(tag + " HTTP outside counter window", request["end_utc_ns"] <= epoch + 100_000_000
                          if phase == "pre" else request["start_utc_ns"] >= epoch + 2_500_000_000)
            check(tag + " retained controller span", row["controller_end_utc_ns"] >=
                  max(sender["end_utc_ns"], receiver["end_utc_ns"]))
        all_marked = [event["marker"] for actor in ("alice", "bob") for event in wires[actor]
                      if (event.get("marker") or "").startswith(f"p8/{public['run_id']}/")]
        check("no omitted or extra trial HTTP", Counter(all_marked) == Counter(used) and len(used) == 4 * len(jobs))
        return {**checks.result(), "configuration": configuration, "split": split, "trials": len(rows),
                "mechanism": mechanism, "observation_sha256": digest(path.read_bytes()),
                "seal_sha256": digest((folder / f"{split}-sealed.json").read_bytes())}
    except Exception as error:
        check("audit cannot evaluate " + type(error).__name__, False)
        return {**checks.result(), "configuration": configuration, "split": split,
                "incomplete": True, "failure_type": type(error).__name__}


def audit_all(data, split):
    results = [audit_cell(data, configuration, split) for configuration in CONFIGURATIONS]
    return {"passed": all(row["passed"] for row in results), "runs": results, "split": split,
            "checks": sum(row["checks"] for row in results),
            "audit_source_sha256": digest((HERE / "audit.py").read_bytes())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("split", choices=("smoke", "calibration", "holdout"))
    args = parser.parse_args()
    result = audit_all(args.data, args.split)
    (args.data / f"{args.split}-audit-results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"passed": result["passed"], "checks": result["checks"],
                      "failures": [row.get("failures") for row in result["runs"] if not row["passed"]]}))
    if not result["passed"]:
        raise SystemExit("P8 audit failed")


if __name__ == "__main__":
    main()
