import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import audit
import gate


class AuditTests(unittest.TestCase):
    def make(self, root, configuration):
        immutable = configuration == "immutable"
        run_id = f"p4-s1-{configuration}"
        roles = ("alice", "bob", "mirror_alice", "mirror_bob") if immutable \
            else ("alice", "bob", "gateway", "upstream")
        netroles = ("alice", "bob") if immutable else ("alice", "bob", "services")
        networks = {role: f"p1-{run_id}-net-{role}" for role in netroles}
        containers = {role: f"p1-{run_id}-{role}" for role in roles}
        repositories = {"alice": "owned-alice", "bob": "owned-bob"} if configuration != "open" \
            else {"shared": "owned-shared"}
        public = {"run_id": run_id, "kind": "immutable" if immutable else "mutable",
                  "configuration": configuration, "gateway_edition": 2,
                  "storage_blocked": configuration == "separate",
                  "negative_cache_period_seconds": 1800, "private_queue_size": 5,
                  "manifest_sha256": "frozen-catalog", "users": {"alice": "user-a", "bob": "user-b"},
                  "networks": networks, "containers": containers, "repositories": repositories}
        if not immutable:
            public["users"]["prefetch"] = "trusted-prefetch"
        boundary = {"containers": {}, "networks": {role: {
            "name": name, "internal": True, "options": {
                "com.docker.network.bridge.gateway_mode_ipv4": "isolated"}}
            for role, name in networks.items()}}
        for role in roles:
            nets = [networks[role]] if role in ("alice", "bob") else \
                [networks[role.removeprefix("mirror_")]] if immutable else \
                list(networks.values()) if role == "gateway" else [networks["services"]]
            boundary["containers"][role] = {
                "name": containers[role], "user": "20001:20001", "root_read_only": True,
                "memory": 134217728, "memory_swap": 134217728, "nano_cpus": 500000000,
                "pids_limit": 48, "cap_drop": ["ALL"], "security_options": ["no-new-privileges:true"],
                "forbidden_environment_present": False, "mounts_read_only": True, "published_ports": {},
                "attached_networks": nets, "bind_mount_destinations": [],
                "network_addresses": {name: "192.0.2.1" if role == "alice" else
                                       "192.0.2.2" if role == "bob" else "192.0.2.3"
                                       for name in nets}}
        public["boundary_readback"] = boundary
        controls = [("bob", "live peer listener on its own network", True),
                    ("alice", "same live listener from the other network", False)]
        for actor in ("alice", "bob"):
            controls.extend([(actor, "assigned serving route" if immutable else "assigned gateway route", True),
                             (actor, "peer serving route" if immutable else "direct backend bypass", False)])
        if not immutable:
            controls.append(("gateway", "backend is reachable from the gateway", True))
        public["network_positive_controls"] = {
            "passed": True, "listener_stopped": True, "listener_exit_code": 0, "checks": len(controls),
            "records": [{"actor": actor, "name": name, "expected_connected": connected,
                         "connected": connected} for actor, name, connected in controls]}
        server = {"run_id": run_id, "gateway_log": [{"ready": True, "edition": 2, "queue_size": 5}],
                  "tenants": {actor: {"response_log": [], "wire_log": [], "startup": [
                      {"ready": True, "edition": 2, "manifest_sha256": "frozen-catalog", "queue_size": 5}]}
                              for actor in ("alice", "bob")},
                  "upstream_requests": 0}
        batches, backend = [], []
        for index, actor in enumerate(("alice", "bob")):
            path = "/fixture/pkg-01.bin" if immutable else \
                f"/{repositories.get(actor) or repositories['shared']}/pkg-01.bin?download=1"
            marker = f"p1/{run_id}/a00/{actor}/r00"
            start = 1_800_000_000_000_000_000 + index * 1_000_000_000
            record = {"actor": actor, "marker": marker, "method": "GET", "path": path,
                      "request_headers": {"Range": "bytes=0-0"}, "scheduled_offset_s": 0,
                      "status": 200, "headers": [], "elapsed_ms": 3, "body_length": 1,
                      "body_text": None, "body_sha256": hashlib.sha256(b"x").hexdigest(),
                      "start_utc_ns": start, "end_utc_ns": start + 3_000_000}
            request = {"method": "GET", "path": path, "headers": {"Range": "bytes=0-0"},
                       "offset_s": 0}
            batches.append({"id": index + 1, "actor": actor, "complete": True,
                            "request": {"id": index + 1, "actor": actor, "requests": [request]},
                            "records": [record]})
            full = path if immutable else "/artifactory" + path
            event = {"marker": marker, "principal": public["users"][actor],
                     "client_ip": "192.0.2.1" if actor == "alice" else "192.0.2.2",
                     "method": "GET", "path": full, "status": 200, "request_headers": {"range": "bytes=0-0"},
                     "start_utc_ns": start + 500_000, "end_utc_ns": start + 2_000_000,
                     "body_sha256": record["body_sha256"], "body_bytes": 1}
            if immutable:
                server["tenants"][actor]["wire_log"].append({**event, "origin_permitted": True})
                server["tenants"][actor]["response_log"].append({**event, "upstream_requests": 0})
            else:
                server["gateway_log"].append({**event, "policy_reason": None, "gateway_edition": 2})
                stamp = datetime.fromtimestamp((start + 500_000) / 1e9, timezone.utc).isoformat()
                backend.append("|".join([stamp, "id", "remote", public["users"][actor], "GET",
                                          path.partition("?")[0], "200", "0", "0", "0", marker]))
                backend.append(json.dumps({"request_User-Agent": marker, "RequestPath": full,
                                            "RequestMethod": "GET", "DownstreamStatus": 200, "StartUTC": stamp, "TraceId": "id", "Duration": 0}))
        self.public, self.server, self.batches, self.backend = public, server, batches, backend
        self.write(root)

    def write(self, root):
        (root / "provision.json").write_text(json.dumps(self.public))
        (root / "server.json").write_text(json.dumps(self.server))
        (root / "server-log-lines.txt").write_text("\n".join(self.backend) + "\n")
        (root / "actions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.batches))
        (root / "cleanup.json").write_text(json.dumps({
            "absence_proved": True, "containers_readback": [], "networks_readback": [],
            "art_a_p1_networks": []}))
        seal = {"complete": True, "scripted": True, "run_id": self.public["run_id"],
                "source_freeze_sha256": "prospective", "batches": len(self.batches),
                "http_requests": sum(len(row["records"]) for row in self.batches),
                "file_sha256": {file.name: gate.sha(file) for file in root.iterdir()
                                if file.is_file() and file.name != "sealed.json"}}
        (root / "sealed.json").write_text(json.dumps(seal))

    def test_complete_join_all_three_configurations(self):
        for configuration in ("open", "separate", "immutable"):
            with self.subTest(configuration=configuration), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make(root, configuration)
                result = audit.audit_remote(root, "prospective")
                self.assertTrue(result["passed"], result["failures"])

    def test_missing_duplicate_and_wrong_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make(root, "separate")
            original = copy.deepcopy(self.server["gateway_log"])
            self.server["gateway_log"].pop()
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])
            self.server["gateway_log"] = original + [copy.deepcopy(original[-1])]
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])
            self.server["gateway_log"] = original
            self.server["gateway_log"][-1]["client_ip"] = "192.0.2.1"
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])

    def test_wire_header_or_body_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make(root, "immutable")
            self.server["tenants"]["alice"]["wire_log"][0]["request_headers"] = {}
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])
            self.make(root, "immutable")
            self.server["tenants"]["alice"]["response_log"][0]["body_sha256"] = "wrong"
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])

    def test_router_query_and_private_network_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make(root, "open")
            row = json.loads(self.backend[1])
            row["RequestPath"] = row["RequestPath"].partition("?")[0]
            self.backend[1] = json.dumps(row)
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])
            self.make(root, "open")
            self.public["boundary_readback"]["containers"]["alice"]["attached_networks"].append(
                self.public["networks"]["bob"])
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])

    def test_seal_and_cleanup_must_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make(root, "separate")
            (root / "cleanup.json").write_text('{"absence_proved":false}')
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])

    def test_native_trace_and_router_completion_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for changes in [{"TraceId": "unrelated"}, {"Duration": 10_000_000_000},
                            {"Duration": -1}]:
                self.make(root, "open")
                row = json.loads(self.backend[1])
                row.update(changes)
                self.backend[1] = json.dumps(row)
                self.write(root)
                self.assertFalse(audit.audit_remote(root, "prospective")["passed"])

    def test_native_admission_identity_is_distinct(self):
        native = Path(__file__).resolve().parent.parent / "P1-meter/data/smoke-s1/open/provision.json"
        original = json.loads(native.read_text())
        self.assertEqual(set(original["users"]), {"alice", "bob", "prefetch"})
        self.assertEqual(len(set(original["users"].values())), 3)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make(root, "open")
            self.public["users"]["prefetch"] = self.public["users"]["bob"]
            self.write(root)
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])
            self.make(root, "separate")
            seal = json.loads((root / "sealed.json").read_text())
            seal["complete"] = False
            (root / "sealed.json").write_text(json.dumps(seal))
            self.assertFalse(audit.audit_remote(root, "prospective")["passed"])


if __name__ == "__main__":
    unittest.main()
