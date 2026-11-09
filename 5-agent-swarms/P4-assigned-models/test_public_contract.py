"""Regression for the mutable public-provision omission found by native smoke."""
from pathlib import Path
import unittest
from unittest.mock import patch

import deployment


class PublicProvision(unittest.TestCase):
    def test_mutable_public_map_matches_owned_state_without_private_fields(self):
        for configuration in ("open", "separate"):
            with self.subTest(configuration=configuration):
                run_id = f"p4-s2-{configuration}"
                state = {
                    "containers": {role: f"p1-{run_id}-{role}"
                                   for role in ("alice", "bob", "gateway", "upstream")},
                    "networks": {role: f"p1-{run_id}-net-{role}"
                                 for role in ("alice", "bob", "services")},
                    "private_test_field": "must not appear in public evidence",
                }
                public = {"run_id": run_id, "kind": "mutable"}
                imported = {key: "fixed" for key in (
                    "p1_service_sha256", "p1_gateway_sha256",
                    "p1_dummy_sha256", "p1_upstream_sha256")}
                with patch.object(deployment.gate, "sources"), \
                        patch.object(deployment.p2, "check_gate"), \
                        patch.object(deployment.p2, "check_sources", return_value=imported), \
                        patch.object(deployment.p2, "sha", return_value="fixed"), \
                        patch.object(deployment.p2.old, "ROOT", Path("/unused")), \
                        patch.object(deployment.p2.old, "provision", return_value=public), \
                        patch.object(deployment.p2, "provision", return_value=public), \
                        patch.object(deployment.p2.old, "load_state", return_value=state), \
                        patch.object(deployment.p2, "replace_gateway"), \
                        patch.object(deployment.p2, "boundary_readback", return_value={}), \
                        patch.object(deployment.p2.preflight, "network_checks", return_value={}):
                    observed = deployment.provision(run_id, configuration, smoke=True)
                self.assertEqual(observed["containers"], state["containers"])
                self.assertEqual(observed["networks"], state["networks"])
                self.assertNotIn("private_test_field", observed)


if __name__ == "__main__":
    unittest.main()
