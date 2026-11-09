"""Complete synthetic cache traces, retained only in temporary test folders."""
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import audit
import configuration
import plan


class CausalAuditTests(unittest.TestCase):
    def fixture(self, root, product="nginx"):
        folder = root / "cohort"
        folder.mkdir()
        (root / "SOURCES-FROZEN.json").write_text("{}")
        run_id = f"p7-g1-{product}-20-shared-cal"
        names = {actor: run_id + "-" + actor for actor in ("alice", "bob")}
        cache, origin = run_id + "-cache", run_id + "-origin"
        nets = {role: run_id + "-net-" + role for role in ("alice", "bob", "services")}
        ips = {"alice": "192.0.2.1", "bob": "192.0.2.2"}
        networks = {name: {"internal": True, "options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}}
                    for name in nets.values()}
        containers = {}
        template = {"user": "20001:20001", "root_read_only": True, "memory": 134217728,
            "memory_swap": 134217728, "nano_cpus": 500000000, "pids_limit": 48,
            "cap_drop": ["ALL"], "security_options": ["no-new-privileges:true"],
            "forbidden_environment_present": False, "published_ports": {}, "mounts_read_only": True,
            "bind_mount_destinations": [], "tmpfs": {"/tmp": "rw,size=64m"}}
        for actor, name in names.items():
            containers[name] = {**template, "image_id": "client-image", "network_addresses": {nets[actor]: ips[actor]}}
        containers[origin] = {**template, "image_id": "client-image", "network_addresses": {nets["services"]: "192.0.2.6"}}
        containers[cache] = {**template, "image_id": "cache-image",
            "memory": 268435456 if product == "squid" else 134217728,
            "memory_swap": 268435456 if product == "squid" else 134217728,
            "pids_limit": 96 if product == "squid" else 48,
            "network_addresses": {nets["alice"]: "192.0.2.3", nets["bob"]: "192.0.2.4", nets["services"]: "192.0.2.5"}}
        controls = [{"actor": actor, "name": name, "expected_connected": connected, "connected": connected}
                    for actor in ("alice", "bob") for name, connected in (
                        ("assigned cache", True), ("direct origin", False), ("live peer listener", actor == "bob"))]
        text = getattr(configuration, product)("shared", {ips[actor]: actor for actor in ips}, origin)
        public = {"run_id": run_id, "effective_arm": "shared", "clients": names,
            "origin": origin, "caches": {actor: cache for actor in names}, "networks": nets,
            "build": {"source_freeze_sha256": audit.sha(root / "SOURCES-FROZEN.json"), "image_id": "cache-image",
                "binaries": {"nginx": {"version": "nginx/1.30.6", "exit_code": 0},
                            "squid": {"version": "Squid Cache: Version 7.8 --enable-http-violations", "exit_code": 0}}},
            "boundary": {"containers": containers, "networks": networks},
            "network_controls": {"passed": True, "listener_stopped": True, "listener_exit_code": 0, "records": controls},
            "configurations": {cache: {"origins": {ips[actor]: actor for actor in ips},
                "configuration": text, "configuration_sha256": hashlib.sha256(text.encode()).hexdigest(), "parse_exit_code": 0}}}
        requests, reservations, access, source = [], [], [], []
        ticks = 1000000000

        def request(actor, marker, path, status=404, cached=False, execute=1, method="GET"):
            nonlocal ticks
            start = ticks
            ticks += 100000000
            duration = 25000000 if not cached and execute and status != 403 else 1000000
            body = b"Missing harmless fixture\n" if status == 404 else b"J controls harmless artifact fixture v1\n"
            if status == 403:
                body = b"Denied"
            if method == "HEAD" or status == 304:
                body = b""
            row = {"actor": actor, "execute": execute, "marker": marker, "method": method, "path": path,
                "headers": {}, "target": f"http://{origin}:8000{path}" if product == "squid" else path,
                "host": cache, "start_utc_ns": start, "end_utc_ns": start + duration,
                "elapsed_ms": duration / 1000000, "status": status if execute else None,
                "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body)}
            if not execute:
                row["idle"] = True
            requests.append(row)
            if execute:
                reservations.append({"marker": marker, "direct_origin": False, "reserved_http_requests": 2, "utc_ns": start - 1})
                ip = "192.0.2.6" if actor == "trusted-source-control" else ips[actor]
                cache_status = ("HIT" if product == "nginx" else "TCP_NEGATIVE_HIT") if cached and status == 404 else \
                    "HIT" if cached else "MISS" if product == "nginx" else "TCP_MISS"
                access.append({"end_epoch_s": str(Decimal(row["end_utc_ns"]) / 1000000000),
                    "client_ip": ip, "actor": "" if status == 403 else actor, "method": method,
                    "path": row["target"], "marker": marker, "status": status, "cache_status": cache_status,
                    "body_bytes": len(body), "request_time_s": str(duration / 1000000000)})
                if not cached and status != 403:
                    source.append({"client_ip": "192.0.2.5", "method": method, "path": path, "marker": marker,
                        "status": status, "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
                        "delay_ms": 20 if status == 404 else 0, "start_utc_ns": start + 1000,
                        "end_utc_ns": start + duration - 1000})
            return row

        canaries = [request("alice", f"p7/{run_id}/canary/cache-0", "/missing/20/canary-cache"),
                    request("alice", f"p7/{run_id}/canary/cache-1", "/missing/20/canary-cache", cached=True)]
        for i in range(6):
            canaries.append(request("bob", f"p7/{run_id}/canary/function-{i}", "/present/canary-functions",
                                    status=200, cached=i > 0, method="HEAD" if i == 2 else "GET"))
        canaries.append(request("trusted-source-control", f"p7/{run_id}/canary/unknown-origin",
                                "/missing/20/canary-denied", status=403))
        public["canaries"] = {"passed": True, "records": canaries,
            "negative_lookup_counts": [1, 0], "expected_lookup_counts": [1, 0]}
        schedule = plan.schedule(product, 20, "shared", "calibration")
        trials = []
        for planned in schedule:
            dispatch = request(planned["sender"], plan.marker(run_id, planned, planned["sender"], "sender"),
                               planned["path"], execute=int(planned["active"]))
            receiver = request(planned["receiver"], plan.marker(run_id, planned, planned["receiver"], "receiver"),
                               planned["path"], cached=planned["active"])
            trials.append({**planned, "sender_dispatch": dispatch,
                "sender_request": dispatch if planned["active"] else None, "receiver_request": receiver,
                "controller_start_utc_ns": dispatch["start_utc_ns"] - 1,
                "sender_dispatch_end_utc_ns": dispatch["end_utc_ns"],
                "controller_end_utc_ns": receiver["end_utc_ns"] + 1})
        if product == "nginx":
            access_text = "".join(json.dumps(row) + "\n" for row in access)
        else:
            access_text = "".join(" ".join((row["end_epoch_s"], row["client_ip"], row["method"],
                row["path"], str(row["status"]), row["cache_status"],
                str(Decimal(row["request_time_s"]) * 1000), row["marker"])) + "\n" for row in access)
        server = {"run_id": run_id, "origin_log": source, "caches": {cache: {"access_log": access_text}}}
        cleanup = {"run_id": run_id, "absence_proved": True, "containers_readback": [],
                   "networks_readback": [], "artifactory_touched": False}
        stop = {"passed": True, "records": [{"name": cache, "signal_exit_code": 0, "process_exit_code": 0}]}
        for name, data in (("provision.json", public), ("server.json", server), ("cleanup.json", cleanup),
                           ("cache-stop.json", stop), ("schedule.json", schedule)):
            (folder / name).write_text(json.dumps(data))
        for name, data in (("trials.jsonl", trials), ("requests.jsonl", requests),
                           ("request-reservations.jsonl", reservations)):
            (folder / name).write_text("".join(json.dumps(row) + "\n" for row in data))
        self.seal(root, folder, product, run_id)
        return folder, server

    def seal(self, root, folder, product, run_id):
        files = [p for p in folder.iterdir() if p.is_file() and p.name != "sealed.json"]
        (folder / "sealed.json").write_text(json.dumps({"complete": True, "scheduled": 12, "completed": 12,
            "product": product, "delay_ms": 20, "arm": "shared", "split": "calibration", "run_id": run_id,
            "cleanup_absence_proved": True, "source_freeze_sha256": audit.sha(root / "SOURCES-FROZEN.json"),
            "request_ledger_sha256_at_seal": audit.sha(folder / "request-reservations.jsonl"),
            "sha256": {p.name: audit.sha(p) for p in files}}))

    def test_complete_synthetic_calibration_passes_for_both_native_log_formats(self):
        for product in ("nginx", "squid"):
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                folder, _ = self.fixture(root, product)
                with patch.object(audit, "HERE", root):
                    result = audit.audit_cohort(folder)
                self.assertTrue(result["passed"], result["failures"])
                self.assertEqual(result["completed"], 12)

    def test_missing_origin_lookup_fails_even_after_files_are_resealed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder, server = self.fixture(root)
            server["origin_log"].pop()
            (folder / "server.json").write_text(json.dumps(server))
            self.seal(root, folder, "nginx", "p7-g1-nginx-20-shared-cal")
            with patch.object(audit, "HERE", root):
                result = audit.audit_cohort(folder)
            self.assertFalse(result["passed"])
            self.assertTrue(any("application-cache cause" in name or "sender origin fill" in name for name in result["failures"]))

    def test_wrong_native_client_identity_fails_with_unchanged_response_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder, server = self.fixture(root)
            cache = next(iter(server["caches"]))
            lines = server["caches"][cache]["access_log"].splitlines()
            row = json.loads(lines[-1])
            row["client_ip"] = "192.0.2.250"
            lines[-1] = json.dumps(row)
            server["caches"][cache]["access_log"] = "\n".join(lines) + "\n"
            (folder / "server.json").write_text(json.dumps(server))
            self.seal(root, folder, "nginx", "p7-g1-nginx-20-shared-cal")
            with patch.object(audit, "HERE", root):
                result = audit.audit_cohort(folder)
            self.assertFalse(result["passed"])
            self.assertTrue(any("native origin/method" in name for name in result["failures"]))


if __name__ == "__main__":
    unittest.main()
