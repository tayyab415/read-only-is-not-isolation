import copy
import unittest

import numpy as np

import analysis
import plan


class AnalysisTests(unittest.TestCase):
    def test_receiver_features_ignore_labels_recipes_and_sender_records(self):
        trial = {"receiver": "bob", "sender": "alice", "label": 1, "recipe": "get",
                 "pre": [], "post": [{
                     "probe": "post.timed.00", "method": "GET", "path": "/owned-bob/pkg-15.bin",
                     "status": 200, "elapsed_ms": 2.0, "start_lag_ms": 0.5, "headers": [],
                     "body_length": 0, "body_sha256": "fixture", "body_text": None}],
                 "sender_records": [{"label": "SECRET"}]}
        users = {"alice": "owned-alice", "bob": "owned-bob"}
        expected = analysis.features(trial, users)
        changed = {**trial, "label": 0, "recipe": "stats", "sender_records": [{"anything": 1000}]}
        self.assertEqual(analysis.features(changed, users), expected)
        self.assertEqual(expected["fam.schedule|start_lag_ms|median"], 0.5)

    def test_arrays_are_preserved_by_imported_feature_extractor(self):
        trial = {"receiver": "bob", "sender": "alice", "pre": [], "post": [{
            "probe": "post.body", "path": "/owned-bob/fixture", "marker": "own-marker",
            "status": 200, "elapsed_ms": 1, "start_lag_ms": 0, "headers": [],
            "body_length": 7, "body_sha256": "unused", "body_text": '{"array":[1,2]}'}]}
        values = analysis.features(trial, {"alice": "owned-alice", "bob": "owned-bob"})
        self.assertEqual(values["post.body|json.array[0]"], 1)
        self.assertEqual(values["post.body|json.array[1]"], 2)

    def test_blocked_null_does_not_confuse_recipe_prediction_with_label_prediction(self):
        schedule = plan.schedule("separate", "holdout", 1000)
        y = np.array([row["label"] for row in schedule])
        recipes = np.array([row["recipe"] for row in schedule])
        senders = np.array([row["sender"] for row in schedule])
        pred = np.array([int(row["recipe"] in ("get", "head")) for row in schedule])
        self.assertEqual(analysis.blocked_permutation_p(pred, y, recipes, senders, 200), 1)

    def test_blocked_permutation_detects_exact_receiver_label_bit(self):
        schedule = plan.schedule("separate", "holdout", 1000)
        y = np.array([row["label"] for row in schedule])
        recipes = np.array([row["recipe"] for row in schedule])
        senders = np.array([row["sender"] for row in schedule])
        self.assertEqual(analysis.blocked_permutation_p(y.copy(), y, recipes, senders, 200), 1 / 201)

    def test_opposite_recipe_effects_confirm_separately_against_matched_idle(self):
        y = np.array([0] * 50 + [1] * 50 + [0] * 50 + [1] * 50)
        recipes = np.array(["get"] * 100 + ["head"] * 100)
        X = np.array([0] * 50 + [1] * 50 + [1] * 50 + [0] * 50, dtype=float).reshape(-1, 1)

        class Encoder:
            columns = ["observable"]
            numeric = ["observable"]

        rows = analysis.confirmations(X, y, recipes, Encoder(), [
            {"feature": "observable", "recipe": "get"},
            {"feature": "observable", "recipe": "head"}])
        self.assertTrue(all(row["p_holm_confirmatory"] < 0.05 for row in rows))
        self.assertEqual([row["effect"] for row in rows], [1, -1])
        self.assertEqual([row["n_neg"] for row in rows], [50, 50])


if __name__ == "__main__":
    unittest.main()
