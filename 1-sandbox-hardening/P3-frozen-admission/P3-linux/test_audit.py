"""Adversarial checks of utility byte attribution and failure retention."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import audit
import deployment
import report


class UtilityAuditTests(unittest.TestCase):
    def test_trace_and_complete_router_interval_are_required(self):
        art = {"trace_id": "same"}
        route = {"TraceId": "same", "StartUTC": "1970-01-01T00:00:00Z",
                 "Duration": 1_000_000}
        event = {"start_utc_ns": 0, "end_utc_ns": 2_000_000}
        self.assertTrue(audit.trace_interval(art, route, event))
        self.assertFalse(audit.trace_interval(art, {**route, "TraceId": "other"}, event))
        self.assertFalse(audit.trace_interval(art, {**route, "Duration": 9_000_000}, event))
        self.assertFalse(audit.trace_interval(art, {**route, "Duration": -1}, event))

    def setUp(self):
        body = b"catalog wheel"
        self.entry = {"canonical": "example", "version": "1", "filename": "example-1.whl",
                      "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        route = f"/files/{self.entry['sha256']}/{self.entry['filename']}"
        self.path = "/task/u00-alice-example" + route
        self.record = {"task_id": "u00-alice-example",
            "pip_report": {"install": [{"metadata": {"name": "example", "version": "1"},
                "download_info": {"url": "http://assigned:8000" + self.path,
                    "archive_info": {"hashes": {"sha256": self.entry["sha256"]}}}}]}}
        self.events = [{"backend_target": route, "path": self.path, "method": "GET", "status": 200,
                        "body_bytes": len(body), "body_sha256": self.entry["sha256"],
                        "backend_body_sha256": self.entry["sha256"]}]
        self.manifest = {"entries": [self.entry]}

    def test_report_without_actual_wheel_request_is_not_evidence(self):
        self.assertFalse(audit.task_downloads(self.record, [], self.manifest, "assigned"))
        self.assertTrue(audit.task_downloads(self.record, self.events, self.manifest, "assigned"))

    def test_wrong_tenant_body_and_backend_hash_are_rejected(self):
        changed = copy.deepcopy(self.record)
        changed["pip_report"]["install"][0]["download_info"]["url"] = \
            "http://peer:8000" + self.path
        self.assertFalse(audit.task_downloads(changed, self.events, self.manifest, "assigned"))
        for field, value in (("body_sha256", "wrong"), ("backend_body_sha256", "wrong"),
                             ("body_bytes", 0), ("status", 403)):
            events = copy.deepcopy(self.events)
            events[0][field] = value
            self.assertFalse(audit.task_downloads(self.record, events, self.manifest, "assigned"))

    def test_extra_unreported_wheel_download_is_rejected(self):
        second = {**self.entry, "filename": "another.whl", "sha256": "ab"}
        manifest = {"entries": [self.entry, second]}
        events = self.events + [{**self.events[0],
            "path": "/task/u00-alice-example/files/ab/another.whl",
            "body_sha256": "ab", "backend_body_sha256": "ab"}]
        self.assertFalse(audit.task_downloads(self.record, events, manifest, "assigned"))

    def test_parser_retains_duplicate_markers_and_full_router_targets(self):
        lines = ['2026-11-08T19:00:00Z|id|ip|user|GET|/repo/file|200|1|2|3|same',
                 '2026-11-08T19:00:00Z|id|ip|user|GET|/repo/file|200|1|2|3|same',
                 json.dumps({"request_User-Agent": "same", "RequestPath": "/artifactory/repo/file?q=x"})]
        backend, router = audit.parse_backend(lines)
        self.assertEqual(len(backend["same"]), 2)
        self.assertEqual(router["same"][0]["RequestPath"], "/artifactory/repo/file?q=x")

    def test_incomplete_arm_reports_the_scheduled_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "sealed.json").write_text(json.dumps({"complete": False}))
            result = audit.audit_arm(folder, "immutable", {})
        self.assertFalse(result["passed"])
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["scheduled"], 28)

    def test_report_rejects_failed_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "audit-results.json").write_text('{"passed":false}')
            with self.assertRaisesRegex(RuntimeError, "independent audit"):
                report.summarize(folder)

    def test_wilson_interval_does_not_call_twenty_eight_successes_certain(self):
        lower, upper = report.wilson(28, 28)
        self.assertGreater(lower, .87)
        self.assertLess(lower, 1)
        self.assertAlmostEqual(upper, 1)


class CompleteSyntheticAuditTests(unittest.TestCase):
    """Synthetic data live only in temporary directories, never the evidence tree."""

    def fixture(self, root):
        own, other = root / "P3-linux", root / "P2-residual"
        own.mkdir()
        other.mkdir()
        (own / "UTILITY-FROZEN.json").write_text("{}")
        (other / "ALLOW-COLLECTION.json").write_text("{}")
        folder = own / "data/u1/immutable"
        folder.mkdir(parents=True)
        schedule = deployment.schedule()
        manifest = json.loads((audit.P3 / "data/manifest.json").read_text())
        entries = {row["canonical"]: row for row in manifest["entries"]}
        check_sha = audit.sha(audit.P3 / "utility.py")
        freeze = {"tasks": schedule, "import_sha256": {"P3-mirror/utility.py": check_sha}}
        networks = {actor: {"name": actor + "-net", "internal": True,
                           "options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}}
                    for actor in ("alice", "bob")}
        containers = {}
        users = {actor: "user-" + actor for actor in ("alice", "bob")}
        for i, role in enumerate(("alice", "bob", "adapter_alice", "adapter_bob", "mirror_alice", "mirror_bob")):
            actor = role.split("_")[-1]
            memory = 268435456 if role in ("alice", "bob") else 134217728
            containers[role] = {"name": role, "user": "20001:20001", "root_read_only": True,
                "memory": memory, "memory_swap": memory, "nano_cpus": 500000000, "pids_limit": 48,
                "cap_drop": ["ALL"], "security_options": ["no-new-privileges:true"],
                "forbidden_environment_present": False, "published_ports": {}, "mounts_read_only": True,
                "network_addresses": {actor + "-net": "192.0.2." + str(i + 1)},
                "bind_mount_destinations": [], "tmpfs": {"/tmp": "rw,size=128m"}, "image_id": "synthetic-image"}
        controls = [{"actor": "bob", "name": "live peer listener on its own network", "connected": True, "expected_connected": True},
                    {"actor": "alice", "name": "same live listener from the other network", "connected": False, "expected_connected": False}]
        adapter_controls = []
        for actor in ("alice", "bob"):
            for name, connected in (("assigned serving route", True), ("peer serving route", False)):
                controls.append({"actor": actor, "name": name, "connected": connected, "expected_connected": connected})
            for name, connected in (("assigned index adapter", True), ("peer index adapter", False)):
                adapter_controls.append({"actor": actor, "name": name, "connected": connected, "expected_connected": connected})
        server = {"run_id": "p3u-m1-immutable", "upstream_requests": 0,
                  "adapter_logs": {"alice": [], "bob": []},
                  "tenants": {actor: {"response_log": [], "wire_log": []} for actor in ("alice", "bob")}}
        direct = []
        tasks = []
        for actor in ("alice", "bob"):
            marker = f"p1/p3u-m1-immutable/utility/control/{actor}"
            event = {"actor": actor, "marker": marker, "method": "GET", "path": "/files/synthetic/denial.whl",
                     "status": 403, "expected_status": 403, "body_sha256": "denied", "body_bytes": 6}
            direct.append(event)
            server["tenants"][actor]["wire_log"].append({**event, "principal": None,
                "origin_permitted": False, "client_ip": containers[actor]["network_addresses"][actor + "-net"]})
        for i, task in enumerate(schedule):
            actor, tid = task["actor"], task["task_id"]
            name = audit.re.sub(r"[-_.]+", "-", task["requirement"].split("==")[0]).lower()
            entry = entries[name]
            route = f"/files/{entry['sha256']}/{entry['filename']}"
            path = f"/task/{tid}" + route
            record = {**task, "platform": "linux", "check_source_sha256": check_sha,
                "check_source_removed": True, "temporary_files_removed": True, "elapsed_s": 1,
                "start_utc_ns": i * 100, "end_utc_ns": i * 100 + 99, "passed": True,
                "pip_exit_code": 0, "function_exit_code": 0, "all_downloads_admitted": True,
                "function_output": '{"function_passed":true}',
                "pip_report": {"install": [{"metadata": {"name": entry["canonical"], "version": entry["version"]},
                    "download_info": {"url": "http://adapter_" + actor + ":8000" + path,
                        "archive_info": {"hashes": {"sha256": entry["sha256"]}}}}]}}
            tasks.append(record)
            event = {"task_id": tid, "actor": actor, "principal": users[actor],
                "client_ip": containers[actor]["network_addresses"][actor + "-net"],
                "adapter_edition": 1, "credential_supplied_to_client": False,
                "start_utc_ns": i * 100 + 1, "end_utc_ns": i * 100 + 10,
                "marker": f"p1/p3u-m1-immutable/utility/{actor}/u{i:06d}",
                "method": "GET", "status": 200, "path": path, "backend_target": route,
                "body_sha256": entry["sha256"], "backend_body_sha256": entry["sha256"],
                "body_bytes": entry["bytes"]}
            server["adapter_logs"][actor].append(event)
            wire = {**event, "path": route, "client_ip":
                    containers["adapter_" + actor]["network_addresses"][actor + "-net"], "origin_permitted": True}
            server["tenants"][actor]["wire_log"].append(wire)
            server["tenants"][actor]["response_log"].append({**wire, "upstream_requests": 0})
        prepared = {"schedule": schedule, "completed_tasks": 28, "run_id": server["run_id"],
            "p1_gate_sha256": audit.sha(other / "ALLOW-COLLECTION.json"),
            "p1_gate": {key: True for key in ("p1_validated", "known_statistics_validated", "known_negative_cache_validated",
                        "p1_causal_audit_passed", "p1_nulls_passed")},
            "boundary_readback": {"containers": containers, "networks": networks},
            "network_positive_controls": {"passed": True, "listener_stopped": True, "listener_exit_code": 0, "records": controls},
            "adapter_route_controls": {"passed": True, "records": adapter_controls},
            "direct_http_controls": {"passed": True, "records": direct}, "initial_provision": {"users": users}}
        cleanup = {"absence_proved": True, "containers_readback": [], "networks_readback": [], "art_a_p1_networks": []}
        (folder / "tasks.jsonl").write_text("".join(json.dumps(row) + "\n" for row in tasks))
        (folder / "prepared.json").write_text(json.dumps(prepared))
        (folder / "server.json").write_text(json.dumps(server))
        (folder / "cleanup.json").write_text(json.dumps(cleanup))
        (folder / "server-log-lines.txt").write_text("")
        self.seal(folder, own)
        return own, folder, freeze, server

    def seal(self, folder, own):
        (folder / "sealed.json").write_text(json.dumps({"complete": True, "scheduled": 28, "completed": 28,
            "passed": 28, "run_id": "p3u-m1-immutable", "cleanup_absence_proved": True,
            "source_freeze_sha256": audit.sha(own / "UTILITY-FROZEN.json"),
            "sha256": {name: audit.sha(folder / name) for name in
                ("tasks.jsonl", "prepared.json", "server.json", "cleanup.json", "server-log-lines.txt")}}))

    def test_complete_fixture_passes_and_duplicate_service_row_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            own, folder, freeze, server = self.fixture(Path(directory))
            with patch.object(audit, "HERE", own):
                result = audit.audit_arm(folder, "immutable", freeze)
                self.assertTrue(result["passed"], result["failures"])
                actor = freeze["tasks"][0]["actor"]
                server["tenants"][actor]["wire_log"].append(server["tenants"][actor]["wire_log"][-1])
                (folder / "server.json").write_text(json.dumps(server))
                self.seal(folder, own)
                result = audit.audit_arm(folder, "immutable", freeze)
                self.assertFalse(result["passed"])
                self.assertTrue(any("one serving request" in name for name in result["failures"]))

    def test_swapped_adapter_origin_fails_even_with_matching_body_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            own, folder, freeze, server = self.fixture(Path(directory))
            actor = freeze["tasks"][0]["actor"]
            server["tenants"][actor]["wire_log"][-1]["client_ip"] = "192.0.2.250"
            (folder / "server.json").write_text(json.dumps(server))
            self.seal(folder, own)
            with patch.object(audit, "HERE", own):
                result = audit.audit_arm(folder, "immutable", freeze)
            self.assertFalse(result["passed"])
            self.assertTrue(any("serving identity and full target" in name for name in result["failures"]))

    def mutable_fixture(self, root):
        own, folder, freeze, server = self.fixture(root)
        prepared = json.loads((folder / "prepared.json").read_text())
        run_id = "p3u-m1-separate"
        server["run_id"] = prepared["run_id"] = run_id
        boundary = prepared["boundary_readback"]
        boundary["networks"]["services"] = {"name": "services-net", "internal": True,
            "options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}}
        for actor in ("alice", "bob"):
            del boundary["containers"]["mirror_" + actor]
        template = copy.deepcopy(boundary["containers"]["adapter_alice"])
        boundary["containers"]["gateway"] = {**template, "name": "gateway", "network_addresses":
            {"alice-net": "192.0.2.11", "bob-net": "192.0.2.12", "services-net": "192.0.2.13"}}
        boundary["containers"]["upstream"] = {**template, "name": "upstream",
            "network_addresses": {"services-net": "192.0.2.14"}}
        controls = prepared["network_positive_controls"]["records"]
        for row in controls:
            if row["name"] == "assigned serving route":
                row["name"] = "assigned gateway route"
            elif row["name"] == "peer serving route":
                row["name"] = "direct backend bypass"
        controls.append({"actor": "gateway", "name": "backend is reachable from the gateway",
                         "connected": True, "expected_connected": True})
        lines, gateway = [], []

        def backend(event, principal):
            path = event.get("backend_target") or event["path"]
            lines.append("|".join(["1970-01-01T00:00:00Z", "id", "ip", principal, event["method"],
                                   path.removeprefix("/artifactory"), str(event["status"]), "1", "2", "3", event["marker"]]))
            lines.append(json.dumps({"request_User-Agent": event["marker"], "StartUTC": "1970-01-01T00:00:00Z",
                "RequestPath": path, "RequestMethod": event["method"], "DownstreamStatus": event["status"], "TraceId": "id", "Duration": 0}))

        for actor, events in server["adapter_logs"].items():
            for event in events:
                event["marker"] = event["marker"].replace("p3u-m1-immutable", run_id)
                event["backend_target"] = "/artifactory/repo-" + actor + event["backend_target"]
                row = {**event, "path": event["backend_target"], "policy_reason": None, "client_ip":
                       boundary["containers"]["adapter_" + actor]["network_addresses"][actor + "-net"]}
                gateway.append(row)
                backend(event, "user-" + actor)
        for event in prepared["direct_http_controls"]["records"]:
            actor = event["actor"]
            event["marker"] = event["marker"].replace("p3u-m1-immutable", run_id)
            event["path"] = "/artifactory/repo-" + actor + event["path"]
            gateway.append({**event, "principal": None, "policy_reason": "origin-identity",
                "client_ip": boundary["containers"][actor]["network_addresses"][actor + "-net"]})
        prepared["trusted_package_prewarm"] = []
        prepared["initial_provision"]["users"]["prefetch"] = "trusted-prefetch"
        prepared["package_source_setup"] = {"artifactory_origin_ip": "192.0.2.15"}
        upstream, responses = [], []
        manifest = json.loads((audit.P3 / "data/manifest.json").read_text())
        for actor in ("alice", "bob"):
            for entry in manifest["entries"]:
                route = f"/files/{entry['sha256']}/{entry['filename']}"
                event = {"actor": actor, "path": "/artifactory/repo-" + actor + route,
                    "marker": f"p1/{run_id}/utility/admission/{actor}/{entry['sha256'][:12]}",
                    "principal": "trusted-prefetch", "method": "GET", "status": 200,
                    "start_utc_ns": 0, "end_utc_ns": 20, "body_sha256": entry["sha256"],
                    "body_bytes": entry["bytes"], "expected_sha256": entry["sha256"], "expected_bytes": entry["bytes"]}
                prepared["trusted_package_prewarm"].append(event)
                backend(event, "trusted-prefetch")
                wire = {**event, "path": route, "principal": "trusted-admission",
                        "client_ip": "192.0.2.15", "origin_permitted": True}
                upstream.append(wire)
                responses.append({**wire, "start_utc_ns": 1, "end_utc_ns": 10, "upstream_requests": 0})
        server.update(gateway_log=gateway, package_upstream_wire_log=upstream,
                      package_upstream_response_log=responses)
        server.pop("tenants")
        (folder / "server.json").write_text(json.dumps(server))
        (folder / "prepared.json").write_text(json.dumps(prepared))
        (folder / "server-log-lines.txt").write_text("\n".join(lines) + "\n")
        seal = {"complete": True, "scheduled": 28, "completed": 28, "passed": 28,
            "run_id": run_id, "cleanup_absence_proved": True,
            "source_freeze_sha256": audit.sha(own / "UTILITY-FROZEN.json"),
            "sha256": {name: audit.sha(folder / name) for name in
                ("tasks.jsonl", "prepared.json", "server.json", "cleanup.json", "server-log-lines.txt")}}
        (folder / "sealed.json").write_text(json.dumps(seal))
        return own, folder, freeze

    def test_trusted_setup_and_runtime_mutable_requests_have_distinct_complete_joins(self):
        with tempfile.TemporaryDirectory() as directory:
            own, folder, freeze = self.mutable_fixture(Path(directory))
            with patch.object(audit, "HERE", own):
                result = audit.audit_arm(folder, "separate", freeze)
            self.assertTrue(result["passed"], result["failures"])
            self.assertEqual(result["forwarded_requests"], 28)
            self.assertEqual(result["rejected_controls"], 2)

    def test_missing_backend_request_fails_after_evidence_is_resealed(self):
        with tempfile.TemporaryDirectory() as directory:
            own, folder, freeze = self.mutable_fixture(Path(directory))
            lines = (folder / "server-log-lines.txt").read_text().splitlines()
            (folder / "server-log-lines.txt").write_text("\n".join(lines[1:]) + "\n")
            seal = json.loads((folder / "sealed.json").read_text())
            seal["sha256"]["server-log-lines.txt"] = audit.sha(folder / "server-log-lines.txt")
            (folder / "sealed.json").write_text(json.dumps(seal))
            with patch.object(audit, "HERE", own):
                result = audit.audit_arm(folder, "separate", freeze)
            self.assertFalse(result["passed"])
            self.assertTrue(any("one Artifactory and router" in name for name in result["failures"]))


if __name__ == "__main__":
    unittest.main()
