import unittest

import _path  # noqa: F401
from common import BC_DIR
from _render_fallback import render_tool_result as fallback
from verify_episode import load_render

CASES = [
    {"rc": 0, "stdout": "hi\n", "stderr": ""},
    {"rc": 2, "stdout": "", "stderr": "boom"},
    {"rc": 0, "stdout": "x" * 5000, "stderr": "y" * 10},
    {"rc": 0, "stdout": "a" * 2500 + "b" * 1500, "stderr": ""},
    {"rc": 137, "stdout": "partial", "stderr": "", "timed_out": True, "timeout_s": 12, "t_start_ns": 0, "t_end_ns": 12 * 10 ** 9},
    {"rc": 137, "stdout": "", "stderr": "", "timed_out": True, "t_start_ns": 5, "t_end_ns": 5 + 30 * 10 ** 9},
]


class RenderAgree(unittest.TestCase):
    def test_fallback_matches_harness_when_present(self):
        path = BC_DIR / "harness" / "render.py"
        if not path.exists():
            self.skipTest("harness/render.py not present yet")
        render, src = load_render()
        self.assertEqual(src, "harness/render.py")
        for c in CASES:
            self.assertEqual(render(c), fallback(c), c)

    def test_fallback_rule(self):
        out = fallback({"rc": 0, "stdout": "a" * 5000, "stderr": ""})
        self.assertTrue(out.startswith("[exit 0]\nSTDOUT:\n"))
        self.assertIn("chars omitted]...", out)
        head = "[exit 0]\nSTDOUT:\n" + "a" * 2480
        self.assertTrue(out.startswith(head))
        total = len("[exit 0]\nSTDOUT:\n") + 5000 + len("\nSTDERR:\n")
        self.assertIn(f"\n...[{total - 3500} chars omitted]...\n", out)
        self.assertTrue(out.endswith("a" * 990 + "\nSTDERR:\n"))


if __name__ == "__main__":
    unittest.main()
