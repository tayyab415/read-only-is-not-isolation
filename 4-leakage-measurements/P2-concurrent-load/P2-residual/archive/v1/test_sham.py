import base64
from http.server import ThreadingHTTPServer
import http.client
import threading
import time
import unittest

import sham


class ShamTests(unittest.TestCase):
    def setUp(self):
        response = {"status": 200, "delay_s": 0, "headers": [["Content-Type", "text/plain"]],
                    "body_b64": base64.b64encode(b"fixed bytes").decode()}
        names = ["get", "head", "cond_get", "range_get", "fresh_get",
                 "repeated_cold", "repeated_warm", "folder_html", "stats"]
        self.profile = {"repository": "owned", "responses":
                        {name: dict(response) for name in names}}
        self.profile["responses"]["repeated_cold"]["delay_s"] = 0.06
        self.profile["responses"]["repeated_cold"]["status"] = 404
        self.profile["responses"]["repeated_warm"]["status"] = 404
        self.profile["responses"]["fresh_get"]["status"] = 404
        self.profile["responses"]["cond_get"].update(status=304, body_b64="")
        self.profile["responses"]["stats"]["status"] = 403
        self.events = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), sham.handler(self.profile, self.events))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        started = time.monotonic()
        client.request(method, path, headers={"User-Agent": "p1/p2-test/owned", **(headers or {})})
        response = client.getresponse()
        result = response.status, response.read(), time.monotonic() - started
        client.close()
        return result

    def test_fixed_body_and_conditional_method_profiles(self):
        path = "/artifactory/owned/pkg-00.bin"
        self.assertEqual(self.request(path)[:2], (200, b"fixed bytes"))
        self.assertEqual(self.request(path, headers={"If-None-Match": '"fixture"'})[:2], (304, b""))
        self.assertEqual(self.request(path, method="HEAD")[:2], (200, b""))

    def test_repeat_cache_is_private_and_fresh_paths_stay_distinct(self):
        path = "/artifactory/owned/fresh/trial-repeat.bin"
        cold, warm = self.request(path), self.request(path)
        self.assertGreater(cold[2], 0.05)
        self.assertLess(warm[2], 0.05)
        self.assertEqual([row["kind"] for row in self.events], ["repeated_cold", "repeated_warm"])
        self.request("/artifactory/owned/fresh/trial-unique-1.bin")
        self.request("/artifactory/owned/fresh/trial-unique-2.bin")
        self.assertEqual([row["kind"] for row in self.events[-2:]], ["fresh_get", "fresh_get"])

    def test_foreign_paths_and_writes_are_rejected(self):
        self.assertEqual(self.request("/artifactory/foreign/pkg-00.bin")[0], 403)
        self.assertEqual(self.request("/artifactory/owned/pkg-00.bin", "PUT")[0], 405)

    def test_wire_headers_are_retained_without_credentials(self):
        self.request("/artifactory/owned/pkg-00.bin",
                     headers={"Range": "bytes=0-0", "Authorization": "Basic private"})
        self.assertEqual(self.events[0]["request_headers"], {"range": "bytes=0-0"})
        self.assertNotIn("private", str(self.events))

    def test_incomplete_or_private_profile_fails_before_serving(self):
        profile = {"repository": "owned", "responses": {}}
        with self.assertRaises(ValueError):
            sham.handler(profile)
        self.profile["responses"]["get"]["headers"] = [["Set-Cookie", "private"]]
        with self.assertRaises(ValueError):
            sham.handler(self.profile)


if __name__ == "__main__":
    unittest.main()
