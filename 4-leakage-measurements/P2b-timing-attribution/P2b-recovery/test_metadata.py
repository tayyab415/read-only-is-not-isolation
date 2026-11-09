"""Exercise P2b's provision seam using its retained edition-3 public fixture."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent / "P2b-attribution"
sys.path.insert(0, str(STUDY))
import p2b


class PublicMetadata(unittest.TestCase):
    def provision(self, missing=False):
        original = STUDY / "archive/v3"
        public = json.loads((original / "data/shared/smoke-provision.json").read_text())
        etag = json.loads((original / "ETAG-READBACK.json").read_text())["shared"]["fixture_etag"]
        boundary = public["boundary_readback"]
        state = {
            "containers": {role: row["name"] for role, row in boundary["containers"].items()},
            "networks": {role: row["name"] for role, row in boundary["networks"].items()},
            "repositories": public["repositories"],
        }
        if not missing:
            state["fixture_etag"] = etag
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / f"gateway-{public['run_id']}.json").write_text("{}")
            with patch.object(p2b.old, "ROOT", root), \
                    patch.object(p2b.old, "free_gib", return_value=4), \
                    patch.object(p2b.old, "provision", return_value=copy.deepcopy(public)), \
                    patch.object(p2b.old, "load_state", return_value=state), \
                    patch.object(p2b.old, "state_path", return_value=root / "state.json"), \
                    patch.object(p2b.old, "wait_ready"), \
                    patch.object(p2b, "start_gateway"), \
                    patch.object(p2b.service, "boundary_readback", return_value=boundary), \
                    patch.object(p2b, "network_checks",
                                 return_value=public["network_positive_controls"]):
                result = p2b.provision(public["run_id"], "shared")
        return result, state

    def test_real_provision_exports_actual_etag_and_unchanged_get_jobs(self):
        public, state = self.provision()
        self.assertEqual(public.get("fixture_etag"), state["fixture_etag"])
        trials = [json.loads(line) for line in
                  (STUDY / "archive/v3/data/shared/smoke.jsonl").read_text().splitlines()]
        for trial in trials:
            expected = p2b.plan.sender_jobs(state, public["run_id"], trial, "separate")
            actual = p2b.plan.sender_jobs(public, public["run_id"], trial, "separate")
            self.assertEqual(actual, expected)
            self.assertTrue(all(row["method"] == "GET" and row["headers"] == {}
                                for row in actual))

    def test_missing_private_metadata_fails_closed(self):
        with self.assertRaises(KeyError):
            self.provision(missing=True)


if __name__ == "__main__":
    unittest.main()
