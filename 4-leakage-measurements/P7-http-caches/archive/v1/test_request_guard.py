"""Request caps are atomic and interrupted reservations remain counted."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import request_guard


def reserve(path, index):
    try:
        request_guard.RequestGuard(path).reserve(f"p7/test/request/{index}")
        return True
    except RuntimeError:
        return False


class RequestGuardTests(unittest.TestCase):
    def test_concurrent_reservations_stop_at_the_conservative_total_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.jsonl"
            with patch.object(request_guard, "MAX_CONTROLLER_REQUESTS", 8), \
                    patch.object(request_guard, "MAX_TOTAL_HTTP_REQUESTS", 6):
                with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                    results = list(pool.map(lambda index: reserve(path, index), range(12)))
            self.assertEqual(sum(results), 3)
            self.assertEqual(sum(json.loads(line)["reserved_http_requests"]
                                 for line in path.read_text().splitlines()), 6)

    def test_direct_origin_calls_still_count_towards_the_controller_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = request_guard.RequestGuard(Path(directory) / "requests.jsonl")
            with patch.object(request_guard, "MAX_CONTROLLER_REQUESTS", 2):
                guard.reserve("p7/test/direct/1", direct_origin=True)
                guard.reserve("p7/test/direct/2", direct_origin=True)
                with self.assertRaisesRegex(RuntimeError, "limit"):
                    guard.reserve("p7/test/direct/3", direct_origin=True)

    def test_failed_dispatch_does_not_release_its_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = request_guard.RequestGuard(Path(directory) / "requests.jsonl")
            with patch.object(request_guard, "MAX_TOTAL_HTTP_REQUESTS", 2):
                guard.reserve("p7/test/interrupted/1")
                with self.assertRaisesRegex(RuntimeError, "limit"):
                    guard.reserve("p7/test/interrupted/2")
            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                guard.reserve("p7/test/interrupted/1")

    def test_corrupted_ledger_stops_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.jsonl"
            path.write_text('{"marker":"p7/test/changed","reserved_http_requests":0}\n')
            with self.assertRaisesRegex(RuntimeError, "malformed"):
                request_guard.RequestGuard(path).reserve("p7/test/next")


if __name__ == "__main__":
    unittest.main()
