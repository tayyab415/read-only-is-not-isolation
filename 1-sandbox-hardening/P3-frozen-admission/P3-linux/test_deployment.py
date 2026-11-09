"""Local orchestration and retention checks; no Docker or VM requests."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import deployment


class DeploymentTests(unittest.TestCase):
    def test_schedule_has_both_tenants_and_every_root_without_duplicate_tasks(self):
        tasks = deployment.schedule()
        self.assertEqual(len(tasks), 28)
        self.assertEqual(len({row["task_id"] for row in tasks}), 28)
        self.assertEqual({row["actor"] for row in tasks}, {"alice", "bob"})
        roots = {row["requirement"] for row in tasks}
        self.assertEqual(len(roots), 14)
        self.assertTrue(all(sum(row["requirement"] == root for row in tasks) == 2 for root in roots))

    def test_direct_http_control_rejects_a_successful_bypass(self):
        state = {"containers": {"alice": "client-alice", "bob": "client-bob", "mirror_alice": "mirror-alice",
                                "mirror_bob": "mirror-bob"}}
        manifest = {"entries": [{"sha256": "ab", "filename": "example.whl"}]}

        def success(args, input, timeout):
            job = json.loads(input)
            return subprocess.CompletedProcess(args, 0, json.dumps({**job, "status": 200}), "")

        with patch.object(deployment.old, "load_state", return_value=state), \
                patch.object(deployment.old, "run", side_effect=success):
            with self.assertRaisesRegex(RuntimeError, "bypassed"):
                deployment.direct_http_controls("p3u-test", "immutable", manifest)

    def test_setup_failure_retains_incomplete_status_and_calls_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            here = Path(temporary)
            catalog = here / "P3-mirror/data"
            catalog.mkdir(parents=True)
            (catalog / "manifest.json").write_text(json.dumps({"entries": []}))
            own = here / "P3-linux"
            own.mkdir()
            (own / "UTILITY-FROZEN.json").write_text("{}")
            other = here / "P2-residual"
            other.mkdir()
            (other / "ALLOW-COLLECTION.json").write_text("{}")
            schedule = deployment.schedule()
            removed = {"absence_proved": True, "containers_readback": [],
                       "networks_readback": [], "art_a_p1_networks": []}
            with patch.object(deployment, "HERE", own), patch.object(deployment, "P3", here / "P3-mirror"), \
                    patch.object(deployment, "P2", other), \
                    patch.object(deployment, "check_freeze", return_value={
                        "tasks": schedule, "arms": ["separate", "immutable"]}), \
                    patch.object(deployment.p2, "check_gate", return_value={}), \
                    patch.object(deployment.p2, "provision", side_effect=RuntimeError("synthetic setup failure")), \
                    patch.object(deployment.old, "state_path", return_value=here / "absent-state.json"), \
                    patch.object(deployment, "cleanup", return_value=removed) as cleanup:
                with self.assertRaisesRegex(RuntimeError, "synthetic setup"):
                    deployment.execute_arm(own / "data/u1", "immutable")
                cleanup.assert_called_once_with("p3u-m1-immutable")
            seal = json.loads((own / "data/u1/immutable/sealed.json").read_text())
            self.assertFalse(seal["complete"])
            self.assertEqual(seal["scheduled"], 28)
            self.assertEqual(seal["completed"], 0)
            self.assertEqual(seal["passed"], 0)
            self.assertTrue(seal["cleanup_absence_proved"])
            self.assertNotIn("sha256", seal)

    def test_ordinary_task_failure_keeps_the_complete_denominator(self):
        with tempfile.TemporaryDirectory() as temporary:
            here = Path(temporary)
            catalog = here / "P3-mirror/data"
            catalog.mkdir(parents=True)
            (catalog / "manifest.json").write_text(json.dumps({"entries": []}))
            own, other = here / "P3-linux", here / "P2-residual"
            own.mkdir()
            other.mkdir()
            (own / "UTILITY-FROZEN.json").write_text("{}")
            (other / "ALLOW-COLLECTION.json").write_text("{}")
            schedule = deployment.schedule()
            removed = {"absence_proved": True, "containers_readback": [],
                       "networks_readback": [], "art_a_p1_networks": []}
            mocked = {
                "utility_clients": None, "start_adapters": {},
                "boundary_readback": {}, "adapter_routes": {},
                "direct_http_controls": {}, "memory_snapshot": {},
                "adapter_failures": None, "collect": ({"run_id": "p3u-m1-immutable"}, []),
                "cleanup": removed,
            }
            with contextlib.ExitStack() as stack:
                for name, value in (("HERE", own), ("P3", here / "P3-mirror"), ("P2", other)):
                    stack.enter_context(patch.object(deployment, name, value))
                stack.enter_context(patch.object(deployment, "check_freeze", return_value={
                    "tasks": schedule, "arms": ["separate", "immutable"]}))
                stack.enter_context(patch.object(deployment.p2, "check_gate", return_value={}))
                stack.enter_context(patch.object(deployment.p2, "provision", return_value={}))
                stack.enter_context(patch.object(deployment.p2.preflight, "network_checks", return_value={}))
                stack.enter_context(patch.object(deployment.old, "state_path", return_value=own / "UTILITY-FROZEN.json"))
                for name, value in mocked.items():
                    stack.enter_context(patch.object(deployment, name, return_value=value))
                stack.enter_context(patch.object(deployment, "task", side_effect=
                    lambda run_id, row, manifest: {**row, "passed": row["task_id"] != schedule[0]["task_id"]}))
                with contextlib.redirect_stdout(io.StringIO()):
                    seal = deployment.execute_arm(own / "data/u1", "immutable")
            self.assertTrue(seal["complete"])
            self.assertEqual(seal["completed"], 28)
            self.assertEqual(seal["passed"], 27)
            self.assertEqual(len(seal["sha256"]), 5)


if __name__ == "__main__":
    unittest.main()
