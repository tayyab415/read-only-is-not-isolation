"""Synthetic trusted-log counterexamples; no live traffic or outcomes."""
from datetime import datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import audit
import plan
import service


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.run_id = "p2-s1-c-separate"
        self.public = {"run_id": self.run_id, "configuration": "separate", "kind": "mutable",
                       "repositories": {"alice": "owned-alice", "bob": "owned-bob"},
                       "users": {"alice": "user-alice", "bob": "user-bob"},
                       "fixture_etag": '"fixture"', "storage_blocked": True,
                       "negative_cache_period_seconds": 1800,
                       "network_positive_controls": {"passed": True, "listener_stopped": True,
                            "listener_exit_code": 0, "checks": 1, "records": [
                                {"connected": True, "expected_connected": True}]},
                       "boundary_readback": {"containers": {"alice": {
                            "user": "20001:20001", "root_read_only": True,
                            "memory": 134217728, "memory_swap": 134217728,
                            "nano_cpus": 500000000, "pids_limit": 48, "cap_drop": ["ALL"],
                            "security_options": ["no-new-privileges:true"],
                            "forbidden_environment_present": False, "mounts_read_only": True,
                            "published_ports": {}}},
                            "networks": {"alice": {"internal": True, "options": {
                                "com.docker.network.bridge.gateway_mode_ipv4": "isolated"}}}}}
        prototype = self.public["boundary_readback"]["containers"]["alice"]
        boundary = self.public["boundary_readback"]
        boundary["networks"] = {role: {"name": f"owned-net-{role}", "internal": True, "options": {
            "com.docker.network.bridge.gateway_mode_ipv4": "isolated"}}
            for role in ("alice", "bob", "services")}
        boundary["containers"] = {role: copy.deepcopy(prototype)
                                  for role in ("alice", "bob", "gateway", "upstream")}
        for index, actor in enumerate(("alice", "bob")):
            boundary["containers"][actor].update(
                attached_networks=[f"owned-net-{actor}"],
                network_addresses={f"owned-net-{actor}": f"192.0.2.{index + 1}"},
                bind_mount_destinations=[])
        controls = [("bob", "live peer listener on its own network", True),
                    ("alice", "same live listener from the other network", False),
                    ("gateway", "backend is reachable from the gateway", True)]
        for actor in ("alice", "bob"):
            controls.extend([(actor, "assigned gateway route", True),
                             (actor, "direct backend bypass", False)])
        self.public["network_positive_controls"].update(
            checks=len(controls), records=[{"actor": actor, "name": name,
                "connected": expected, "expected_connected": expected}
                for actor, name, expected in controls])
        self.schedule = plan.schedule("separate", "calibration", 8)
        self.trials, self.gateway, self.backend = [], [], []
        empty_sha = hashlib.sha256(b"").hexdigest()
        for index, trial in enumerate(self.schedule):
            epoch = 1_800_000_000_000_000_000 + index * 10_000_000_000
            record = {**trial, "epoch_utc_ns": epoch, "pre": [], "post": [], "sender_records": [],
                      "batch_must_stop": False, "failed_request_markers": [],
                      "scheduled_sender_jobs": 128, "receiver_schedule_lead_ms": 250.0,
                      "controller_start_utc_ns": epoch - 300_000_000,
                      "controller_end_utc_ns": epoch + 4_000_000_000}
            for actor, key, jobs in [
                (trial["receiver"], "post", plan.receiver_jobs(self.public, self.run_id, trial)),
                (trial["sender"], "sender_records",
                 plan.sender_jobs(self.public, self.run_id, trial, "separate"))]:
                for job in jobs:
                    start = epoch + int(job["offset_s"] * 1e9)
                    storage = job["path"].startswith("/api/storage/")
                    status = 403 if storage else 200
                    row = {**job, "actor": actor, "target": "gateway",
                           "request_headers": job["headers"], "headers": [], "status": status,
                           "scheduled_offset_s": job["offset_s"], "schedule_epoch_utc_ns": epoch,
                           "schedule_setup_utc_ns": epoch - 250_000_000,
                           "start_lag_ms": 0, "elapsed_ms": 3, "start_utc_ns": start,
                           "end_utc_ns": start + 3_000_000, "body_length": 0,
                           "body_sha256": empty_sha, "body_text": None}
                    record[key].append(row)
                    self.gateway.append({
                        "marker": job["marker"], "principal": self.public["users"][actor],
                        "client_ip": "192.0.2.1" if actor == "alice" else "192.0.2.2",
                        "method": job["method"], "path": "/artifactory" + job["path"],
                        "status": status, "policy_reason": "storage" if storage else None,
                        "request_headers": list(audit.lower_headers(job["headers"]).items()),
                        "body_bytes": 0, "body_sha256": empty_sha, "gateway_edition": 2})
                    if not storage:
                        timestamp = datetime.fromtimestamp(start / 1e9, timezone.utc).isoformat()
                        fields = [timestamp, "id", "remote", self.public["users"][actor], job["method"],
                                  job["path"].partition("?")[0], str(status), "0", "0", "0", job["marker"]]
                        self.backend.append("|".join(fields))
                        self.backend.append(json.dumps({"request_User-Agent": job["marker"],
                            "RequestPath": "/artifactory" + job["path"], "RequestMethod": job["method"],
                            "DownstreamStatus": status, "StartUTC": timestamp}))
            self.trials.append(record)
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        split = "calibration"
        (self.folder / f"{split}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.trials))
        (self.folder / f"{split}-provision.json").write_text(json.dumps(self.public))
        (self.folder / f"{split}-schedule.json").write_text(json.dumps(self.schedule))
        (self.folder / f"{split}-server.json").write_text(json.dumps({
            "run_id": self.run_id, "gateway_log": self.gateway}))
        (self.folder / f"{split}-server-log-lines.txt").write_text("\n".join(self.backend) + "\n")
        (self.folder / f"{split}-cleanup.json").write_text(json.dumps({
            "absence_proved": True, "containers_readback": [], "networks_readback": [], "art_a_p1_networks": []}))
        seal = {"complete": True, "run_id": self.run_id, "trials": 8,
                "sha256": service.sha(self.folder / f"{split}.jsonl"),
                "provision_sha256": service.sha(self.folder / f"{split}-provision.json"),
                "evidence_sha256": {name: service.sha(self.folder / name) for name in [
                    f"{split}-schedule.json", f"{split}-server.json",
                    f"{split}-server-log-lines.txt", f"{split}-cleanup.json"]}}
        (self.folder / f"{split}-sealed.json").write_text(json.dumps(seal))

    def result(self):
        return audit.audit_split(self.folder, "separate", "calibration", smoke=True)

    def test_complete_exact_join_passes(self):
        result = self.result()
        self.assertTrue(result["passed"], result["failures"])

    def test_missing_or_duplicate_gateway_row_fails(self):
        saved = self.gateway.pop()
        self.write()
        self.assertFalse(self.result()["passed"])
        self.gateway.extend([saved, saved])
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_independent_wire_header_mismatch_fails(self):
        self.gateway[0]["request_headers"] = [["range", "bytes=1-1"]]
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_router_query_loss_fails(self):
        index = next(index for index, line in enumerate(self.backend) if line.startswith("{"))
        row = json.loads(self.backend[index])
        row["RequestPath"] += "?unreported=1"
        self.backend[index] = json.dumps(row)
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_body_hash_mismatch_and_changed_schedule_fail(self):
        self.gateway[0]["body_sha256"] = "wrong"
        self.write()
        self.assertFalse(self.result()["passed"])
        self.gateway[0]["body_sha256"] = hashlib.sha256(b"").hexdigest()
        self.trials[0]["post"][0]["scheduled_offset_s"] += 0.1
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_missing_boundary_inventory_or_network_control_fails(self):
        saved = self.public["boundary_readback"]["containers"].pop("bob")
        self.write()
        self.assertFalse(self.result()["passed"])
        self.public["boundary_readback"]["containers"]["bob"] = saved
        self.public["network_positive_controls"]["records"].pop()
        self.write()
        self.assertFalse(self.result()["passed"])

    def test_wrong_network_origin_and_forged_dispatch_lead_fail(self):
        original_ip = self.gateway[0]["client_ip"]
        self.gateway[0]["client_ip"] = "192.0.2.254"
        self.write()
        self.assertFalse(self.result()["passed"])
        self.gateway[0]["client_ip"] = original_ip
        self.trials[0]["receiver_schedule_lead_ms"] += 1
        self.write()
        self.assertFalse(self.result()["passed"])


if __name__ == "__main__":
    unittest.main()
