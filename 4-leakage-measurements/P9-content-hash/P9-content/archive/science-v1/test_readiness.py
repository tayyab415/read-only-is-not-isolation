"""Real retained-native and arithmetic checks before any P9 native fixture."""
from collections import Counter
import copy
import importlib.util
import itertools
import json
from pathlib import Path
import unittest

import analyze
import gate
import native_storage as storage
import p2b

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "p9_independent_arithmetic", BASE / "P2b-verification/verify.py")
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class Readiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = json.loads((BASE / "P2-storage-native/RESULT.json").read_text())["native"]
        cls.public = json.loads((BASE / "P4-models/remote-data/assigned-m1/"
                                 "p4-m1-luna-separate/provision.json").read_text())
        cls.public["arm"] = "equal"
        cls.mapping = {
            "passed": True, "nodes": result["native_nodes"],
            "artifactory_image": result["artifactory_image"],
            "blobs": {result["blob_sha1"]: {
                "path": result["existing_canonical_blob_path"],
                "sha1": result["blob_sha1"], "sha256": result["blob_sha256"],
                "stat": result["blob_stat"]}}}

    def test_actual_completed_native_mapping_passes(self):
        self.assertTrue(storage.validate_mapping(self.mapping, self.public))

    def test_actual_mapping_is_insufficient_for_distinct_cell(self):
        altered = {**self.public, "arm": "distinct"}
        self.assertFalse(storage.validate_mapping(self.mapping, altered))

    def test_missing_native_path_refuses(self):
        altered = copy.deepcopy(self.mapping)
        altered["nodes"].pop()
        self.assertFalse(storage.validate_mapping(altered, self.public))

    def test_wrong_node_checksum_refuses(self):
        altered = copy.deepcopy(self.mapping)
        altered["nodes"][0]["sha256"] = "0" * 64
        self.assertFalse(storage.validate_mapping(altered, self.public))

    def test_wrong_canonical_blob_refuses(self):
        altered = copy.deepcopy(self.mapping)
        next(iter(altered["blobs"].values()))["sha256"] = "0" * 64
        self.assertFalse(storage.validate_mapping(altered, self.public))

    def test_unexpected_native_node_path_refuses(self):
        altered = copy.deepcopy(self.mapping)
        altered["nodes"][0]["node_path"] = "other"
        self.assertFalse(storage.validate_mapping(altered, self.public))

    def test_rendered_source_changes_only_fixed_cell_flag(self):
        equal = storage.rendered_upstream("equal")
        distinct = storage.rendered_upstream("distinct")
        self.assertEqual(distinct, equal.replace("DISTINCT = False", "DISTINCT = True"))
        self.assertEqual(equal, storage.rendered_upstream("aa-equal"))
        self.assertEqual(distinct, storage.rendered_upstream("aa-distinct"))
        compile(equal, "equal-native-upstream", "exec")
        compile(distinct, "distinct-native-upstream", "exec")

    def test_receiver_content_is_constant_and_sender_length_matches(self):
        for cell in p2b.ARMS:
            self.assertEqual(storage.payload(cell, 15), storage.PAYLOAD_A)
            self.assertEqual(len(storage.payload(cell, 0)), 31)
        self.assertNotEqual(storage.payload("equal", 0), storage.payload("distinct", 0))

    def test_every_schedule_balanced_within_direction(self):
        for cell in p2b.ARMS:
            for split, n in p2b.SIZES.items():
                rows = p2b.schedule(cell, split)
                self.assertEqual(len(rows), n)
                self.assertEqual(Counter((r["sender"], r["label"]) for r in rows),
                                 Counter({(sender, label): n // 4
                                          for sender in ("alice", "bob") for label in (0, 1)}))
                self.assertTrue(all(r["recipe"] == "get" for r in rows))

    def test_primary_hypergeometric_matches_exact_assignment_counts(self):
        # All two-direction balanced eight-label assignments and fixed predictions.
        assignments = list(itertools.combinations(range(4), 2))
        for left in assignments:
            for right in assignments:
                labels = [int(i in left) for i in range(4)] + [
                    int(i in right) for i in range(4)]
                trials = [{"label": label, "sender": "alice" if i < 4 else "bob"}
                          for i, label in enumerate(labels)]
                for predictions in ((0,) * 8, (1,) * 8,
                                    (0, 0, 1, 1, 1, 0, 1, 0), tuple(labels)):
                    exact = reference.conditional_tail([
                        (row["label"], prediction, row["sender"])
                        for row, prediction in zip(trials, predictions)])
                    self.assertAlmostEqual(analyze.conditional_pvalue(trials, predictions),
                                           float(exact), places=13)

    def test_unbalanced_primary_assignments_refuse(self):
        trials = [{"label": 0, "sender": sender} for sender in ("alice", "bob")]
        with self.assertRaisesRegex(RuntimeError, "balanced"):
            analyze.conditional_pvalue(trials, [0, 0])

    def test_strict_decoder_ties_predict_zero(self):
        for direction in (True, False):
            self.assertEqual(analyze.decode(
                {"threshold_ms": 4.0, "active_is_below": direction}, 4.0), 0)

    def test_incomplete_earlier_evidence_gate_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "closure"):
            gate.validate_evidence({})

    def test_scientific_modules_resolve_to_this_study(self):
        self.assertEqual(Path(p2b.__file__).resolve().parent, BASE / "P9-content")
        self.assertEqual(Path(analyze.__file__).resolve().parent, BASE / "P9-content")
        import audit
        self.assertEqual(Path(audit.__file__).resolve().parent, BASE / "P9-content")


if __name__ == "__main__":
    unittest.main()
