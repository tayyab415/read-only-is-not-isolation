"""Offline counterexamples for P2b's prospective evidence and freeze gates."""
import json
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import analyze
import audit as native_audit
import p2b
import gateway_pad


REAL_HERE = Path(__file__).resolve().parent


class ResponseFloor(unittest.TestCase):
    def test_actual_http_headers_wait_for_response_floor(self):
        events = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), gateway_pad.handler({
            "origin_binding": {}, "repos": {}, "min_response_ms": 12,
        }, events))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        try:
            connection.request("HEAD", "/artifactory/owned-fixture/wheel")
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertEqual(response.read(), b"")
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.assertEqual(len(events), 1)
        record = events[0]
        self.assertGreaterEqual(record["headers_started_utc_ns"] - record["start_utc_ns"],
                                12_000_000)
        self.assertGreaterEqual(record["end_utc_ns"], record["headers_started_utc_ns"])


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class Readiness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.here = self.root / "P2b-attribution"
        self.p2 = self.root / "P2-residual"
        self.here.mkdir()
        self.p2.mkdir()
        for name in p2b.SOURCE_NAMES:
            (self.here / name).write_bytes((REAL_HERE / name).read_bytes())
        (self.here / "SOURCES-SHA256.txt").write_text("".join(
            f"{p2b.sha(self.here / name)}  {name}\n" for name in sorted(p2b.SOURCE_NAMES)))
        self.patches = [
            patch.object(p2b, "HERE", self.here),
            patch.object(p2b, "P2", self.p2),
            patch.object(analyze, "HERE", self.here),
            patch.object(p2b.service, "check_sources", return_value={}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def calibration(self, count=100):
        data = self.here / "data"
        runs = []
        for arm in analyze.ARMS:
            folder = data / arm
            folder.mkdir(parents=True)
            trials = [{
                "label": i % 2, "trial_id": f"c{i:04d}",
                "post": [{"probe": f"post.timed.{j:02d}", "elapsed_ms": 4 - i % 2}
                         for j in range(15)],
            } for i in range(count)]
            (folder / "calibration.jsonl").write_text(
                "".join(json.dumps(t) + "\n" for t in trials))
            hashes = {}
            for suffix in ("provision.json", "schedule.json", "server.json",
                           "server-log-lines.txt", "cleanup.json"):
                path = folder / f"calibration-{suffix}"
                path.write_text("synthetic metadata fixture")
                hashes[path.name] = p2b.sha(path)
            seal = {
                "complete": True, "cleanup_absence_proved": True, "trials": count,
                "sha256": p2b.sha(folder / "calibration.jsonl"),
                "evidence_sha256": hashes,
                "source_manifest_sha256": p2b.sha(self.here / "SOURCES-SHA256.txt"),
            }
            write(folder / "calibration-sealed.json", seal)
            runs.append({"arm": arm, "split": "calibration", "trials": count,
                         "passed": True, "seal_sha256": p2b.sha(folder / "calibration-sealed.json")})
        write(data / "audit-calibration.json", {
            "passed": True, "runs": runs,
            "audit_source_sha256": p2b.sha(self.here / "audit.py"),
            "source_manifest_sha256": p2b.sha(self.here / "SOURCES-SHA256.txt"),
        })
        return data

    def gate(self):
        evidence = self.here / "gate-evidence"
        write(self.p2 / "SOURCES-FROZEN.json", {"synthetic": True})
        (self.p2 / "audit.py").write_text("synthetic audit source")
        write(self.p2 / "FROZEN.json", {"synthetic": True})
        files = {
            "FROZEN.json": {"synthetic": True},
            "holdout-audit-results.json": {
                "passed": True, "smoke": False, "source_sha256": p2b.sha(self.p2 / "audit.py"),
                "runs": [{"configuration": arm, "split": "holdout", "trials": 1000, "passed": True}
                         for arm in p2b.plan.CONFIGURATIONS],
            },
            "holdout-results.json": {
                "aa_nominal_pass": True,
                "results": {arm: {"trials": 1000} for arm in p2b.plan.CONFIGURATIONS},
            },
        }
        for arm in p2b.plan.CONFIGURATIONS:
            folder = self.p2 / "data/p2-m1" / arm
            folder.mkdir(parents=True)
            for name in ("holdout.jsonl", "holdout-provision.json", "holdout-cleanup.json"):
                (folder / name).write_text("synthetic held metadata")
            seal = {"complete": True, "trials": 1000, "cleanup_absence_proved": True,
                    "sha256": p2b.sha(folder / "holdout.jsonl"),
                    "provision_sha256": p2b.sha(folder / "holdout-provision.json"),
                    "evidence_sha256": {"holdout-cleanup.json": p2b.sha(folder / "holdout-cleanup.json")}}
            write(folder / "holdout-sealed.json", seal)
            files[f"holdout-seal-{arm}.json"] = seal
        for name, value in files.items():
            write(evidence / name, value)
        gate = {
            "p2_holdout_audit_passed": True, "p2_aa_null_passed": True,
            "p2_source_freeze_sha256": p2b.sha(self.p2 / "SOURCES-FROZEN.json"),
            "evidence_sha256": {name: p2b.sha(evidence / name) for name in files},
        }
        write(self.here / "P2-GATE.json", gate)
        return gate

    def test_unbacked_booleans_rejected(self):
        write(self.here / "P2-GATE.json",
              {"p2_holdout_audit_passed": True, "p2_aa_null_passed": True})
        with self.assertRaises(RuntimeError):
            p2b.p2_gate()

    def test_complete_gate_metadata_accepted(self):
        gate = self.gate()
        self.assertEqual(gate, p2b.p2_gate())

    def test_hashed_audit_tampering_rejected(self):
        self.gate()
        (self.here / "gate-evidence/holdout-audit-results.json").write_text("{}")
        with self.assertRaises(RuntimeError):
            p2b.p2_gate()

    def test_rehashed_failed_audit_rejected(self):
        gate = self.gate()
        path = self.here / "gate-evidence/holdout-audit-results.json"
        audit = json.loads(path.read_text())
        audit["passed"] = False
        write(path, audit)
        gate["evidence_sha256"][path.name] = p2b.sha(path)
        write(self.here / "P2-GATE.json", gate)
        with self.assertRaises(RuntimeError):
            p2b.p2_gate()

    def test_changed_held_input_rejected(self):
        self.gate()
        (self.p2 / "data/p2-m1/separate/holdout.jsonl").write_text("changed")
        with self.assertRaises(RuntimeError):
            p2b.p2_gate()

    def test_passing_calibration_freezes_and_verifies(self):
        data = self.calibration()
        frozen = analyze.freeze(data)
        self.assertEqual(frozen, analyze.verify_freeze(data))
        self.assertEqual(frozen["decoders"]["shared"]["threshold_ms"], 3.5)

    def test_freeze_after_holdout_rejected(self):
        data = self.calibration()
        (data / "shared/holdout.jsonl").write_text("{}\n")
        with self.assertRaises(RuntimeError):
            analyze.freeze(data)

    def test_refreeze_rejected(self):
        data = self.calibration()
        analyze.freeze(data)
        with self.assertRaises(RuntimeError):
            analyze.freeze(data)

    def test_short_calibration_rejected(self):
        with self.assertRaises(RuntimeError):
            analyze.freeze(self.calibration(count=2))

    def test_missing_calibration_audit_rejected(self):
        data = self.calibration()
        (data / "audit-calibration.json").unlink()
        with self.assertRaises(FileNotFoundError):
            analyze.freeze(data)

    def test_rehashed_failed_calibration_audit_rejected(self):
        data = self.calibration()
        path = data / "audit-calibration.json"
        audit = json.loads(path.read_text())
        audit["passed"] = False
        write(path, audit)
        with self.assertRaises(RuntimeError):
            analyze.freeze(data)

    def test_calibration_mutation_after_freeze_rejected(self):
        data = self.calibration()
        analyze.freeze(data)
        (data / "shared/calibration.jsonl").write_text("{}\n")
        with self.assertRaises(RuntimeError):
            analyze.verify_freeze(data)

    def test_source_mutation_after_freeze_rejected(self):
        data = self.calibration()
        analyze.freeze(data)
        (self.here / "gateway_pad.py").write_text("changed")
        with self.assertRaises(RuntimeError):
            analyze.verify_freeze(data)

    def test_audit_metadata_mutation_after_freeze_rejected(self):
        data = self.calibration()
        analyze.freeze(data)
        path = data / "audit-calibration.json"
        audit = json.loads(path.read_text())
        audit["extra"] = "changed"
        write(path, audit)
        with self.assertRaises(RuntimeError):
            analyze.verify_freeze(data)

    def test_split_audit_needs_all_arms(self):
        data = self.calibration()
        path = data / "audit-calibration.json"
        audit = json.loads(path.read_text())
        audit["runs"].pop()
        write(path, audit)
        with self.assertRaises(RuntimeError):
            analyze.check_split_audit(data, "calibration")

    def native_fixture(self):
        source = REAL_HERE.parent / "P2-residual/data/smoke-p2-s2/separate"
        folder = self.here / "native-smoke"
        folder.mkdir()
        for suffix in (".jsonl", "-provision.json", "-schedule.json", "-server.json",
                       "-server-log-lines.txt", "-cleanup.json"):
            (folder / f"smoke{suffix}").write_bytes((source / f"calibration{suffix}").read_bytes())
        public = json.loads((folder / "smoke-provision.json").read_text())
        public.update(arm="shared", pad_ms=0)
        write(folder / "smoke-provision.json", public)
        trial_rows = [json.loads(line) for line in (folder / "smoke.jsonl").read_text().splitlines()]
        keys = ("label", "recipe", "sender", "receiver", "token", "index", "split", "trial_id")
        expected = [{key: row[key] for key in keys} for row in trial_rows]
        seal = {"complete": True, "run_id": public["run_id"], "trials": 8}
        self.reseal_native(folder, seal)
        return folder, expected

    def reseal_native(self, folder, seal=None):
        if seal is None:
            seal = json.loads((folder / "smoke-sealed.json").read_text())
        seal.update(
            sha256=p2b.sha(folder / "smoke.jsonl"),
            source_manifest_sha256=p2b.sha(self.here / "SOURCES-SHA256.txt"),
            evidence_sha256={f"smoke-{suffix}": p2b.sha(folder / f"smoke-{suffix}")
                for suffix in ("provision.json", "schedule.json", "server.json",
                               "server-log-lines.txt", "cleanup.json")})
        write(folder / "smoke-sealed.json", seal)

    def native_check(self, folder, expected):
        with patch.object(native_audit, "HERE", self.here), \
                patch.object(p2b, "schedule", return_value=expected):
            return native_audit.audit_arm(folder, "shared", "smoke")

    def test_retained_native_smoke_joins_pass(self):
        folder, expected = self.native_fixture()
        result = self.native_check(folder, expected)
        self.assertTrue(result["passed"], result["failures"])

    def test_resealed_wrong_router_target_rejected(self):
        folder, expected = self.native_fixture()
        path = folder / "smoke-server-log-lines.txt"
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("{"):
                row = json.loads(line)
                row["RequestPath"] += "/wrong"
                lines[i] = json.dumps(row)
                break
        path.write_text("\n".join(lines) + "\n")
        self.reseal_native(folder)
        result = self.native_check(folder, expected)
        self.assertFalse(result["passed"])
        self.assertTrue(any("backend identity and trace" in f for f in result["failures"]))

    def test_resealed_duplicate_router_row_rejected(self):
        folder, expected = self.native_fixture()
        path = folder / "smoke-server-log-lines.txt"
        lines = path.read_text().splitlines()
        lines.append(next(line for line in lines if line.startswith("{")))
        path.write_text("\n".join(lines) + "\n")
        self.reseal_native(folder)
        self.assertFalse(self.native_check(folder, expected)["passed"])

    def test_resealed_missing_network_positive_control_rejected(self):
        folder, expected = self.native_fixture()
        path = folder / "smoke-provision.json"
        public = json.loads(path.read_text())
        public["network_positive_controls"]["records"].pop()
        write(path, public)
        self.reseal_native(folder)
        self.assertFalse(self.native_check(folder, expected)["passed"])


if __name__ == "__main__":
    unittest.main()
