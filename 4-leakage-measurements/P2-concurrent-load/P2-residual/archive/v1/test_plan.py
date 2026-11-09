from collections import Counter
import copy
import json
import unittest

import plan
import worker


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.state = {"repositories": {"alice": "owned-alice", "bob": "owned-bob"},
                      "fixture_etag": '"owned-etag"'}
        self.rows = plan.schedule("separate", "calibration", 300)

    def test_reproducible_and_balanced_within_recipe_and_direction(self):
        self.assertEqual(self.rows, plan.schedule("separate", "calibration", 300))
        for count in (300, 1000):
            rows = plan.schedule("separate", "holdout", count)
            self.assertEqual(Counter(row["label"] for row in rows), {0: count // 2, 1: count // 2})
            cells = Counter((row["recipe"], row["sender"], row["label"]) for row in rows)
            for recipe in plan.RECIPES:
                for sender in ("alice", "bob"):
                    self.assertEqual(cells[recipe, sender, 0], cells[recipe, sender, 1])

    def test_receiver_jobs_do_not_depend_on_label_or_recipe(self):
        trial = self.rows[0]
        expected = plan.receiver_jobs(self.state, "p2-test", trial)
        for recipe in plan.RECIPES:
            for label in (0, 1):
                modified = {**trial, "recipe": recipe, "label": label}
                self.assertEqual(plan.receiver_jobs(self.state, "p2-test", modified), expected)
        self.assertNotIn('"label"', json.dumps(expected))
        self.assertNotIn('"recipe"', json.dumps(expected))

    def test_sender_and_receiver_paths_stay_in_assigned_repository(self):
        for trial in self.rows:
            for actor, jobs in [
                (trial["sender"], plan.sender_jobs(self.state, "p2-test", trial, "separate")),
                (trial["receiver"], plan.receiver_jobs(self.state, "p2-test", trial))]:
                self.assertTrue(all(plan.scoped(row["path"], self.state, actor) for row in jobs))
                worker.validate_jobs(jobs)

    def test_each_active_recipe_is_bounded_to_fixed_count_and_window(self):
        for recipe in plan.RECIPES:
            trial = {**self.rows[0], "label": 1, "recipe": recipe}
            jobs = plan.sender_jobs(self.state, "p2-test", trial, "separate")
            self.assertEqual(len(jobs), 128)
            self.assertLess(max(row["offset_s"] for row in jobs), 2)
            self.assertEqual(len({row["marker"] for row in jobs}), 128)

    def test_idle_and_aa_senders_always_get_empty_schedule(self):
        for trial in self.rows:
            self.assertEqual(plan.sender_jobs(self.state, "p2-test", trial, "aa"), [])
            self.assertEqual(plan.sender_jobs(self.state, "p2-test", {**trial, "label": 0}, "separate"), [])

    def test_fresh_paths_are_unique_and_repeated_path_is_constant(self):
        trial = {**self.rows[0], "label": 1}
        fresh = plan.sender_jobs(self.state, "p2-test", {**trial, "recipe": "fresh_get"}, "separate")
        repeat = plan.sender_jobs(self.state, "p2-test", {**trial, "recipe": "repeated_get"}, "separate")
        self.assertEqual(len({row["path"] for row in fresh}), 128)
        self.assertEqual(len({row["path"] for row in repeat}), 1)

    def test_receiver_during_and_after_windows_are_fixed(self):
        jobs = plan.receiver_jobs(self.state, "p2-test", self.rows[0])
        self.assertEqual(len(jobs), 21)
        self.assertTrue(all(row["offset_s"] < 2 for row in jobs[:15]))
        self.assertTrue(all(2 < row["offset_s"] < 3 for row in jobs[15:]))
        worker.validate_jobs(jobs)

    def test_immutable_uses_admitted_paths_and_local_absences(self):
        state = {**self.state, "kind": "immutable",
                 "object_paths": {"0": "/files/hash/a.whl", "15": "/files/hash/b.whl"}}
        trial = {**self.rows[0], "label": 1, "recipe": "get"}
        jobs = plan.sender_jobs(state, "p2-test", trial, "immutable")
        self.assertTrue(all(row["path"] == state["object_paths"]["0"] for row in jobs))
        self.assertTrue(all(plan.scoped(row["path"], state, trial["receiver"])
                            for row in plan.receiver_jobs(state, "p2-test", trial)))

    def test_bad_counts_or_configuration_fail(self):
        for count in [0, 3, 302]:
            with self.assertRaises(ValueError):
                plan.schedule("separate", "calibration", count)
        with self.assertRaises(ValueError):
            plan.schedule("unknown", "calibration", 300)


if __name__ == "__main__":
    unittest.main()
