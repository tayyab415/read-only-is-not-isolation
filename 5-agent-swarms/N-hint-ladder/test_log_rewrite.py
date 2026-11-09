"""Observed internal directory rewrite, with strict negative controls."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from verify import backend_path_matches, router_target_matches, verify_cohort

DATA = Path(__file__).resolve().parent / "data"


class RewriteTests(unittest.TestCase):
    def test_mapping_only_exact_root_get(self):
        self.assertTrue(backend_path_matches("GET", "/repo/", "/api/nativeBrowser", "repo"))
        self.assertTrue(backend_path_matches("GET", "/repo-cache/", "/api/nativeBrowser", "repo"))
        self.assertTrue(backend_path_matches("GET", "/repo/?properties", "/api/nativeBrowser", "repo"))
        self.assertTrue(backend_path_matches("GET", "/repo/?list&stats=1", "/api/nativeBrowser", "repo"))
        for method, path in [
            ("HEAD", "/repo/"), ("GET", "/repo/pkg.bin"),
            ("GET", "/repo/pkg.bin?properties"), ("HEAD", "/repo/?properties"),
            ("GET", " /repo/"), ("GET", "/re\npo/"),
            ("GET", "/api/storage/repo/pkg.bin?stats"), ("GET", "/other/"),
        ]:
            self.assertFalse(backend_path_matches(method, path, "/api/nativeBrowser", "repo"))

    def test_actual_root_listing_all_other_evidence_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cohort"
            shutil.copytree(DATA / "b3-33", copy)
            self.assertEqual(verify_cohort(copy)["failed"], [])

    def test_actual_root_queries_all_other_evidence_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cohort"
            shutil.copytree(DATA / "c1-13", copy)
            self.assertEqual(verify_cohort(copy)["failed"], [])

    def test_actual_query_serialization_all_other_evidence_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cohort"
            shutil.copytree(DATA / "c1-11", copy)
            self.assertEqual(verify_cohort(copy)["failed"], [])

    def test_query_encoding_preserves_every_other_target_part(self):
        path = "/repo/pkg.bin?properties;stats"
        self.assertTrue(router_target_matches(path, "/artifactory/repo/pkg.bin?properties%3Bstats"))
        for wrong in [
            "/artifactory/repo/other.bin?properties%3Bstats",
            "/artifactory/repo/pkg.bin?properties%253Bstats",
            "/artifactory/repo/pkg.bin?properties%3Bstats&x=1",
            "/artifactory/repo/pkg.bin?properties%3bstats",
            " /artifactory/repo/pkg.bin?properties%3Bstats",
            "/artifactory/repo/pkg.\nbin?properties%3Bstats",
        ]:
            self.assertFalse(router_target_matches(path, wrong))
        self.assertFalse(router_target_matches("/repo/pkg.bin?stats",
                                                "/artifactory/repo/pkg.bin?properties"))
        self.assertFalse(router_target_matches("/repo/pkg.bin?x;y#f;g",
                                                "/artifactory/repo/pkg.bin?x%3By#f%3Bg"))

    def test_actual_property_root_listing_all_other_evidence_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cohort"
            shutil.copytree(DATA / "c1-19", copy)
            self.assertEqual(verify_cohort(copy)["failed"], [])

    def test_actual_cache_root_listing_all_other_evidence_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cohort"
            shutil.copytree(DATA / "b2-14", copy)
            self.assertEqual(verify_cohort(copy)["failed"], [])

    def test_native_browser_cannot_replace_artifact_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cohort"
            shutil.copytree(DATA / "b3-01", copy)
            server = json.loads((copy / "server.json").read_text())
            rows = server["artifactory_request_log_lines"]
            for i, line in enumerate(rows):
                if line.endswith("|n/b3-01/alice/1"):
                    fields = line.split("|")
                    fields[5] = "/api/nativeBrowser"
                    rows[i] = "|".join(fields)
                    break
            else:
                self.fail("expected exact download marker missing")
            (copy / "server.json").write_text(json.dumps(server))
            report = verify_cohort(copy)
            self.assertTrue(any(c["name"] == "exact HTTP join n/b3-01/alice/1"
                                for c in report["failed"]))


if __name__ == "__main__":
    unittest.main()
