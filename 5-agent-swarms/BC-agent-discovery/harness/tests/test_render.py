import unittest

import _path  # noqa: F401
from render import render_tool_result


def ex(**kw):
    base = {"rc": 0, "stdout": "", "stderr": "", "timed_out": False, "t_start_ns": 0, "t_end_ns": 0}
    return {**base, **kw}


class RenderTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(render_tool_result(ex(stdout="hi\n", rc=0)),
                         "[exit 0]\nSTDOUT:\nhi\n\nSTDERR:\n")

    def test_nonzero_and_stderr(self):
        self.assertEqual(render_tool_result(ex(rc=2, stderr="boom")),
                         "[exit 2]\nSTDOUT:\n\nSTDERR:\nboom")

    def test_timeout_uses_clipped_timeout(self):
        out = render_tool_result(ex(timed_out=True, rc=137, timeout_s=17))
        self.assertTrue(out.startswith("[timed out after 17s]\n"))

    def test_timeout_falls_back_to_duration(self):
        out = render_tool_result(ex(timed_out=True, rc=137, t_start_ns=0, t_end_ns=5_000_000_000))
        self.assertTrue(out.startswith("[timed out after 5s]\n"))

    def test_exactly_4000_not_clipped(self):
        base = len(render_tool_result(ex()))
        out = render_tool_result(ex(stdout="x" * (4000 - base)))
        self.assertEqual(len(out), 4000)
        self.assertNotIn("omitted", out)

    def test_4001_clipped(self):
        base = len(render_tool_result(ex()))
        full = f"[exit 0]\nSTDOUT:\n{'x' * (4001 - base)}\nSTDERR:\n"
        self.assertEqual(len(full), 4001)
        out = render_tool_result(ex(stdout="x" * (4001 - base)))
        self.assertEqual(out, full[:2500] + "\n...[501 chars omitted]...\n" + full[-1000:])

    def test_long_keeps_head_and_tail(self):
        stdout = "".join(chr(97 + i % 26) for i in range(9000))
        full = f"[exit 0]\nSTDOUT:\n{stdout}\nSTDERR:\n"
        out = render_tool_result(ex(stdout=stdout))
        self.assertTrue(out.startswith(full[:2500]))
        self.assertTrue(out.endswith(full[-1000:]))
        self.assertIn(f"\n...[{len(full) - 3500} chars omitted]...\n", out)
        self.assertEqual(len(out), 2500 + 1000 + len(f"\n...[{len(full) - 3500} chars omitted]...\n"))

    def test_deterministic_and_pure(self):
        e = ex(stdout="a" * 5000)
        before = dict(e)
        self.assertEqual(render_tool_result(e), render_tool_result(e))
        self.assertEqual(e, before)


if __name__ == "__main__":
    unittest.main()
