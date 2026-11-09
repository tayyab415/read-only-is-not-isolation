"""Retain the native one-byte Range failure as a regression fixture."""
import hashlib
import json
from pathlib import Path
import unittest

import service


class ProfileBodyTests(unittest.TestCase):
    def test_native_range_hash_is_reconstructed_exactly(self):
        path = Path(__file__).resolve().parent / "native-fixtures/range-profile.json"
        native = json.loads(path.read_text())["row"]
        self.assertEqual(native["status"], 206)
        self.assertEqual(native["body_bytes"], 1)
        row = {"status": native["status"], "body_length": native["body_bytes"],
               "body_text": None, "body_sha256": native["body_sha256"]}
        self.assertEqual(service.body_bytes(row), b"p")

    def test_wrong_range_hash_still_fails(self):
        with self.assertRaises(RuntimeError):
            service.body_bytes({"status": 206, "body_length": 1, "body_text": None,
                                "body_sha256": hashlib.sha256(b"x").hexdigest()})

    def test_unexpected_body_size_still_fails(self):
        with self.assertRaises(RuntimeError):
            service.body_bytes({"status": 200, "body_length": 2, "body_text": None,
                                "body_sha256": hashlib.sha256(b"pp").hexdigest()})
