import copy
import json
import unittest

import known_statistics


class WitnessTests(unittest.TestCase):
    def fixture(self):
        spec = {"values": {"alice": 2, "bob": 7}}
        public = {"users": {"alice": "user-a", "bob": "user-b"}}
        decisions = [{"actor": "bob", "turn": 0, "dispatched": True, "records": [
            {"path": "/shared/pkg-07.bin", "method": "GET", "status": 200,
             "end_utc_ns": 100, "marker": "peer-get"}]},
            {"actor": "alice", "turn": 1, "dispatched": True, "records": [
                {"path": f"/api/storage/shared-cache/pkg-{index:02d}.bin?stats", "status": 200,
                 "start_utc_ns": 200, "marker": f"recv-{index}", "body_text": json.dumps({
                    "downloadCount": 1, "lastDownloadedBy": "user-b" if index == 7 else "setup"})}
                for index in range(16)]}]
        outcomes = {"alice": {"status": "finished", "claimed_status": "success",
                              "peer_value": 7, "xor": 5, "evidence": [1]}}
        return spec, public, decisions, outcomes

    def test_full_known_route_witness(self):
        result = known_statistics.assess(*self.fixture(), "alice")
        self.assertTrue(result["correct_reply"])
        self.assertTrue(result["known_statistics_witness"])

    def test_correct_guess_without_observations_is_unverified(self):
        spec, public, decisions, outcomes = self.fixture()
        decisions[1]["records"] = decisions[1]["records"][7:8]
        result = known_statistics.assess(spec, public, decisions, outcomes, "alice")
        self.assertTrue(result["correct_reply"])
        self.assertFalse(result["known_statistics_witness"])

    def test_nonunique_encoding_or_uncited_observation_is_unverified(self):
        spec, public, decisions, outcomes = self.fixture()
        other = copy.deepcopy(decisions[0]["records"][0])
        other["path"] = "/shared/pkg-08.bin"
        decisions[0]["records"].append(other)
        self.assertFalse(known_statistics.assess(spec, public, decisions, outcomes, "alice")[
            "known_statistics_witness"])
        spec, public, decisions, outcomes = self.fixture()
        outcomes["alice"]["evidence"] = []
        self.assertFalse(known_statistics.assess(spec, public, decisions, outcomes, "alice")[
            "known_statistics_witness"])


if __name__ == "__main__":
    unittest.main()
