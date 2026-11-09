import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
from node import FakeNode, InfraFailure, SSHNode

# A stand-in `ssh` that records its argv and stdin and prints one JSON object.
FAKE_SSH = """#!/bin/bash
cat > "$FAKE_SSH_DIR/stdin"
printf '%s\\n' "$@" > "$FAKE_SSH_DIR/argv"
case "$FAKE_SSH_MODE" in
  ok) echo '{"ok": true, "rows": []}' ;;
  fail) echo "denied" >&2; exit 255 ;;
  badjson) echo 'not json' ;;
  list) echo '[1]' ;;
esac
"""


class SSHNodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        ssh = d / "ssh"
        ssh.write_text(FAKE_SSH)
        ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
        self.old = dict(os.environ)
        os.environ["PATH"] = f"{d}:{os.environ['PATH']}"
        os.environ["FAKE_SSH_DIR"] = str(d)
        self.d = d

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        self.tmp.cleanup()

    def mode(self, m):
        os.environ["FAKE_SSH_MODE"] = m

    def argv(self):
        return (self.d / "argv").read_text().splitlines()

    def test_exec_command_line_and_stdin(self):
        self.mode("ok")
        r = SSHNode("bc-n01").exec("ep1", 2, 30, "echo 'hi there'")
        self.assertEqual(r, {"ok": True, "rows": []})
        argv = self.argv()
        self.assertIn("bc-n01", argv)
        self.assertEqual(argv[-1], "sudo /opt/bc/node_ctl.sh exec ep1 2 30")
        self.assertIn("ControlMaster=auto", argv)
        self.assertEqual((self.d / "stdin").read_text(), "echo 'hi there'")

    def test_setup_sends_tar_bytes(self):
        self.mode("ok")
        SSHNode("bc-n01").setup("ep1", 1, "ep1-A", "abcd", b"\x1f\x8bBINARY")
        self.assertEqual(self.argv()[-1], "sudo /opt/bc/node_ctl.sh setup ep1 1 ep1-A abcd")
        self.assertEqual((self.d / "stdin").read_bytes(), b"\x1f\x8bBINARY")

    def test_gwlog_args(self):
        self.mode("ok")
        SSHNode("bc-n01").gwlog(5, 9)
        self.assertEqual(self.argv()[-1], "sudo /opt/bc/node_ctl.sh gwlog 5 9")

    def test_nonzero_rc_bad_json_and_non_object(self):
        for m in ("fail", "badjson", "list"):
            self.mode(m)
            with self.assertRaises(InfraFailure, msg=m):
                SSHNode("bc-n01").status()

    def test_unsafe_argument_rejected(self):
        self.mode("ok")
        with self.assertRaises(InfraFailure):
            SSHNode("bc-n01").collect("ep1; rm -rf /", 1)


class FakeNodeTests(unittest.TestCase):
    def test_exec_timeout_and_caps(self):
        n = FakeNode()
        n.setup("e", 1, "t", "k", b"")
        r = n.exec("e", 1, 1, "sleep 5")
        self.assertTrue(r["timed_out"])
        self.assertEqual(r["rc"], 137)
        r = n.exec("e", 1, 5, "head -c 70000 /dev/zero | tr '\\0' x; echo err >&2; exit 3")
        self.assertEqual((r["rc"], r["stdout_bytes"], len(r["stdout"]), r["stderr"]), (3, 70000, 65536, "err\n"))
        n.teardown("e", 1)


if __name__ == "__main__":
    unittest.main()
