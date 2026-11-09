import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import audit
import experiment as e


def accounting(actor):
    return {"cgroup_v2": True, "cpu_max": "50000 100000\n", "memory_max": "134217728\n",
            "self_cgroup": "0::/\n", "mountinfo": "1 0 0:1 / /sys/fs/cgroup ro - cgroup2 cgroup rw\n",
            "cgroup": {"raw": "usage_usec 10\n", "values": {"usage_usec": 10}},
            "namespace_ids": {kind: f"{actor}-{kind}" for kind in ("cgroup", "net", "pid", "mnt")},
            "cpu_stat": {"file_sha256": e.digest(e.static_view(4))}}


def boundary(configuration, mask_path):
    containers = {}
    for role in ("alice", "bob", "mirror_alice", "mirror_bob"):
        actor = "alice" if "alice" in role else "bob"
        client = role in ("alice", "bob")
        mounts = [{"source": str(mask_path), "destination": "/proc/stat", "rw": False}] \
            if role == "bob" and e.masked(configuration) else []
        if not client:
            mounts = [{"source": "/owned/" + name, "destination": "/" + name, "rw": False}
                      for name in ("mirror.py", "deploy.py", "catalog", "config.json")]
        containers[role] = {
            "name": role, "image_id": "sha256:" + "1" * 64,
            "user": "20001:20001", "root_read_only": True, "cap_drop": ["ALL"],
            "memory": 134217728, "memory_swap": 134217728, "nano_cpus": 500000000,
            "pids_limit": 48, "forbidden_environment_present": False,
            "security_options": ["no-new-privileges:true"], "privileged": False,
            "pid_mode": "", "ipc_mode": "private", "published_ports": {},
            "attached_networks": ["net-" + actor], "cgroupns_mode": "private",
            "tmpfs": {"/tmp": "rw,nosuid,nodev,size=" + ("16m" if client else "64m")},
            "bind_mounts": mounts, "network_addresses": {"net-" + actor: "10.0." + ("1" if actor == "alice" else "2") + ".2"}}
    return {"containers": containers, "networks": {actor: {
        "internal": True, "options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
        "containers": sorted([actor, "mirror_" + actor])} for actor in ("alice", "bob")}}


def fixture(configuration, split="smoke"):
    run_id = f"p8-{'s1' if split == 'smoke' else 'm1'}-{split[0]}-{configuration}"
    mask = e.static_view(4)
    routes = [{"name": name, "connected": connected, "expected_connected": connected}
              for name, connected in (("live peer listener on its own network", True),
                                      ("same live listener from the other network", False),
                                      ("assigned serving route", True), ("peer serving route", False),
                                      ("assigned serving route", True), ("peer serving route", False))]
    public = {"run_id": run_id, "kind": "immutable", "configuration": configuration,
              "networks": {"alice": "net-alice", "bob": "net-bob"},
              "containers": {role: role for role in ("alice", "bob", "mirror_alice", "mirror_bob")},
              "users": {"alice": "identity-alice", "bob": "identity-bob"},
              "mask_path": "/owned/stat.txt", "mask_bytes_b64": base64.b64encode(mask).decode(),
              "mask_sha256": e.digest(mask), "boundary": boundary(configuration, Path("/owned/stat.txt")),
              "host_inventory": {"cpu_count": 4, "clock_ticks": 100,
                                 "namespace_ids": {kind: "host-" + kind for kind in ("cgroup", "net", "pid", "mnt")}},
              "network_controls": {"passed": True, "listener_stopped": True, "listener_exit_code": 0,
                                   "checks": 6, "records": routes}}
    file_task = {"successful": True, "sha256": e.digest(e.FILE_BYTES), "bytes": 1024}
    readiness = {actor: {"ready": True, "file_task": file_task.copy(), "inventory": accounting(actor)}
                 for actor in ("alice", "bob")}
    server = {"kind": "immutable", "upstream_requests": 0, "tenants": {
        actor: {"response_log": [], "wire_log": []} for actor in ("alice", "bob")}}
    rows = []
    for index, job in enumerate(e.schedule(configuration, split)):
        epoch = 1794000000000000000 + index * 3_000_000_000
        base = {"op": "trial", "epoch_utc_ns": epoch, "token": job["token"]}
        flag = e.executing(configuration, job["label"])
        row = {**job, "configuration": configuration, "epoch_utc_ns": epoch,
               "controller_start_utc_ns": epoch - 700_000_000, "controller_end_utc_ns": epoch + 2_700_000_000,
               "batch_must_stop": False, "failures": []}
        for actor in ("alice", "bob"):
            participant = {"actor": actor, "rpc": base.copy(), "setup_utc_ns": epoch - 600_000_000,
                           "file": file_task.copy(), "http": [], "end_utc_ns": epoch + 2_600_000_000}
            if actor == "alice":
                participant["rpc"]["execute"] = flag
                participant["load"] = {"execute": flag, "iterations": 20000 if flag else 0,
                                       "process_cpu_ns": 1_000_000_000 if flag else 1_000_000,
                                       "planned_start_utc_ns": epoch + 200_000_000,
                                       "planned_end_utc_ns": epoch + 2_200_000_000,
                                       "start_utc_ns": epoch + 200_000_000, "end_utc_ns": epoch + 2_200_000_000,
                                       "cgroup_before": {"values": {"usage_usec": 10}},
                                       "cgroup_after": {"values": {"usage_usec": 1000010 if flag else 1010}}}
            for phase in ("pre", "post"):
                marker = f"p8/{run_id}/{job['token']}/{actor}/{phase}"
                start = epoch - 500_000_000 if phase == "pre" else epoch + 2_500_000_000
                request = {"method": "GET", "path": "/fixture/pkg-00.bin", "marker": marker,
                           "request_headers": {}, "start_utc_ns": start, "end_utc_ns": start + 2_000_000,
                           "status": 200, "body_length": len(e.PAYLOAD), "body_sha256": e.digest(e.PAYLOAD)}
                participant["http"].append(request)
                body = {key: value for key, value in request.items() if key not in ("request_headers", "body_length")}
                body.update(tenant=actor, body_bytes=len(e.PAYLOAD), upstream_requests=0)
                wire = {key: value for key, value in request.items() if key not in ("body_length", "body_sha256")}
                wire.update(client_ip=public["boundary"]["containers"][actor]["network_addresses"]["net-" + actor],
                            principal=public["users"][actor], origin_permitted=True,
                            identity_source="trusted network origin", error=None)
                server["tenants"][actor]["response_log"].append(body)
                server["tenants"][actor]["wire_log"].append(wire)
            row["sender" if actor == "alice" else "receiver"] = participant
        observer_samples, host_samples = [], []
        for sample_index, offset in enumerate(e.OFFSETS):
            busy = sample_index * (20 if flag else 5)
            line = f"cpu {1000 + busy} 0 0 {1000 + 40 * sample_index - busy} 0 0 0 0 0 0"
            moment = epoch + round(offset * 1e9)
            record = {"cpu_line": line, "file_sha256": e.digest(line.encode()),
                      "read_start_utc_ns": moment, "read_end_utc_ns": moment + 10_000,
                      "scheduled_offset_s": offset, "schedule_epoch_utc_ns": epoch,
                      "schedule_setup_utc_ns": epoch - 400_000_000, "start_lag_ms": 0.0}
            host_samples.append(record.copy())
            if e.masked(configuration):
                record.update(cpu_line=mask.splitlines()[0].decode(), file_sha256=e.digest(mask))
            record["cgroup"] = {"raw": f"usage_usec {10 + sample_index}\n",
                                "values": {"usage_usec": 10 + sample_index}}
            observer_samples.append(record)
        row["receiver"].update(samples=observer_samples, view_after=observer_samples[-1].copy())
        row["host_samples"] = host_samples
        rows.append(row)
    cleanup = {"absence_proved": True, "containers_readback": [], "networks_readback": [],
               "art_a_p1_networks": []}
    return {"public": public, "readiness": readiness, "server": server, "rows": rows, "cleanup": cleanup}


def write_fixture(root, configuration, contents, omit_hash=None, split="smoke"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "SOURCES-FROZEN.json").write_text('{"test_only":true}')
    folder = root / configuration
    folder.mkdir()
    contents["public"]["source_freeze_sha256"] = e.digest((root / "SOURCES-FROZEN.json").read_bytes())
    values = {"schedule": e.schedule(configuration, split), "provision": contents["public"],
              "readiness": contents["readiness"], "server": contents["server"], "cleanup": contents["cleanup"]}
    for name, value in values.items():
        (folder / f"{split}-{name}.json").write_text(json.dumps(value))
    (folder / f"{split}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in contents["rows"]))
    seal = {"complete": True, "trials": len(contents["rows"]), "retained_trials": len(contents["rows"]),
            "cleanup_absence_proved": True,
            "sha256": e.digest((folder / f"{split}.jsonl").read_bytes()),
            "evidence_sha256": {f"{split}-{name}.json": e.digest((folder / f"{split}-{name}.json").read_bytes())
                               for name in values if name != omit_hash}}
    (folder / f"{split}-sealed.json").write_text(json.dumps(seal))


class AuditTests(unittest.TestCase):
    def run_case(self, configuration="native", change=None, omit_hash=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = fixture(configuration)
            if change:
                change(contents)
            write_fixture(root, configuration, contents, omit_hash)
            with patch.object(audit, "HERE", root):
                return audit.audit_cell(root, configuration, "smoke")

    def test_all_four_complete_audit_fixtures(self):
        for configuration in e.CONFIGURATIONS:
            result = self.run_case(configuration)
            self.assertTrue(result["passed"], result)

    def test_wrong_origin_fails_after_consistent_reseal(self):
        result = self.run_case(change=lambda data: data["server"]["tenants"]["bob"]["wire_log"][0].update(client_ip="wrong"))
        self.assertFalse(result["passed"])

    def test_wrong_identity_fails(self):
        result = self.run_case(change=lambda data: data["server"]["tenants"]["bob"]["wire_log"][0].update(principal="alice"))
        self.assertFalse(result["passed"])

    def test_missing_native_wire_fails(self):
        self.assertFalse(self.run_case(change=lambda data: data["server"]["tenants"]["bob"]["wire_log"].pop())["passed"])

    def test_extra_trial_wire_fails(self):
        def change(data):
            data["server"]["tenants"]["bob"]["wire_log"].append(copy.deepcopy(
                data["server"]["tenants"]["bob"]["wire_log"][0]))
        self.assertFalse(self.run_case(change=change)["passed"])

    def test_receiver_label_fails(self):
        self.assertFalse(self.run_case(change=lambda data: data["rows"][0]["receiver"]["rpc"].update(label=1))["passed"])

    def test_wrong_effective_cgroup_scope_fails(self):
        self.assertFalse(self.run_case(change=lambda data: data["readiness"]["bob"]["inventory"].update(
            self_cgroup="0::/parent/child\n"))["passed"])

    def test_mount_name_alone_does_not_pass(self):
        self.assertFalse(self.run_case(change=lambda data: data["readiness"]["bob"]["inventory"].update(
            mountinfo="1 0 0:1 / /sys/fs/cgroup ro - tmpfs tmpfs rw\n"))["passed"])

    def test_host_namespace_fails(self):
        self.assertFalse(self.run_case(change=lambda data: data["readiness"]["bob"]["inventory"]["namespace_ids"].update(
            cgroup="host-cgroup"))["passed"])

    def test_changing_static_mask_fails(self):
        self.assertFalse(self.run_case("masked", change=lambda data: data["rows"][0]["receiver"]["samples"][1].update(
            file_sha256="changed"))["passed"])

    def test_writable_mask_mount_fails(self):
        self.assertFalse(self.run_case("masked", change=lambda data: data["public"]["boundary"]["containers"]["bob"]
                                     ["bind_mounts"][0].update(rw=True))["passed"])

    def test_unmounted_simulated_mask_fails(self):
        self.assertFalse(self.run_case("masked", change=lambda data: data["public"]["boundary"]["containers"]["bob"].update(
            bind_mounts=[]))["passed"])

    def test_no_work_active_sender_fails(self):
        def change(data):
            next(row for row in data["rows"] if row["label"] == 1)["sender"]["load"]["iterations"] = 0
        self.assertFalse(self.run_case(change=change)["passed"])

    def test_wrong_sample_offset_fails(self):
        self.assertFalse(self.run_case(change=lambda data: data["rows"][0]["receiver"]["samples"][1].update(
            scheduled_offset_s=.999))["passed"])

    def test_container_local_cpu_counters_do_not_match_host(self):
        def change(data):
            for sample in data["rows"][0]["receiver"]["samples"]:
                sample["cpu_line"] = "cpu 1 0 0 2 0 0 0 0 0 0"
        self.assertFalse(self.run_case(change=change)["passed"])

    def test_omitted_sealed_evidence_fails(self):
        self.assertFalse(self.run_case(omit_hash="provision")["passed"])

    def test_failed_cleanup_fails(self):
        self.assertFalse(self.run_case(change=lambda data: data["cleanup"].update(absence_proved=False))["passed"])

    def test_wrong_file_readback_fails(self):
        self.assertFalse(self.run_case(change=lambda data: data["rows"][0]["receiver"]["file"].update(sha256="wrong"))["passed"])

    def test_mismatched_images_fail(self):
        self.assertFalse(self.run_case(change=lambda data: data["public"]["boundary"]["containers"]["bob"].update(
            image_id="sha256:" + "2" * 64))["passed"])


if __name__ == "__main__":
    unittest.main()
