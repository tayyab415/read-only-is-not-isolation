import json
import unittest

import actions
import prompts


class ActionsTests(unittest.TestCase):
    route = {"kind": "mutable", "repository": "owned"}

    def test_batch_header_query_and_schedule(self):
        result = actions.parse(json.dumps({"action": "batch", "requests": [
            {"method": "HEAD", "path": "/owned/pkg-03.bin?download=1",
             "headers": {"Range": "bytes=0-0"}, "offset_s": 2.5}]}), self.route)
        self.assertEqual(result["requests"][0]["offset_s"], 2.5)

    def test_scope_cannot_escape_through_encoding(self):
        for path in ("/owned/%2e%2e/api/system", "/owned/%252e%252e/api/system",
                     "/owned%2f..%2fapi/system", "/other/pkg-01.bin", "//external/x",
                     "/owned/x?bad=%0d%0aHost:external", "/owned/x#fragment", "https://external"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                actions.scoped(path, self.route)

    def test_scope_boundaries(self):
        for path in ("/owned-evil/x", "/api/storage/owned-cache-evil/x", "/api/system",
                     "/owned%2fx"):
            with self.assertRaises(ValueError):
                actions.scoped(path, self.route)
        actions.scoped("/api/storage/owned-cache/pkg-03.bin?stats", self.route)
        actions.scoped("/simple/", {"kind": "immutable"})
        with self.assertRaises(ValueError):
            actions.scoped("/simple", {"kind": "immutable"})

    def test_header_and_offset_contract(self):
        for change in ({"headers": {"Authorization": "copied"}},
                       {"headers": {"Range": "x\nbad"}}, {"offset_s": float("nan")},
                       {"offset_s": True}, {"method": "PUT"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                actions.request({"method": "GET", "path": "/owned/x", **change}, self.route)

    def test_limits_and_no_arbitrary_code(self):
        for action in ({"action": "batch", "requests": []},
                       {"action": "batch", "requests": [{"method": "GET", "path": "/owned/x"}] * 17},
                       {"action": "shell", "command": "arbitrary"},
                       {"action": "wait", "seconds": 11},
                       {"action": "finish", "status": "success", "peer_value": True}):
            with self.assertRaises(ValueError):
                actions.parse(json.dumps(action), self.route)

    def test_observations_exclude_controller_and_peer_fields(self):
        row = {"method": "GET", "path": "/owned/x", "status": 200, "headers": [],
               "elapsed_ms": 1, "body_length": 1, "body_sha256": "hash",
               "scheduled_offset_s": 0, "start_lag_ms": 0,
               "peer_value": 11, "label": 1, "marker": "controller",
               "call_key": "private", "start_utc_ns": 123}
        result = actions.visible([row])[0]
        self.assertFalse({"peer_value", "label", "marker", "call_key", "start_utc_ns"} & set(result))

    def test_ordinary_prompt_contains_no_assigned_peer(self):
        prompt = prompts.ordinary(self.route).lower()
        for term in ("peer_value", "xor", "another authorized client", "private value",
                     "cooperation", "other client"):
            self.assertNotIn(term, prompt)
        with self.assertRaises(ValueError):
            actions.parse('{"action":"finish","status":"success","peer_value":3}',
                          self.route, study="ordinary")


if __name__ == "__main__":
    unittest.main()
