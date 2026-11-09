"""Node access: SSHNode (real, via node_ctl.sh over ssh) and FakeNode (local, for tests).

Both expose the same methods; every call returns the parsed JSON object from the
node, or raises InfraFailure.
  setup(ep, slot, tenant_id, api_key, workspace_tar)   exec(ep, slot, timeout_s, script)
  canary(ep, slot)  collect(ep, slot)  teardown(ep, slot)
  gwlog(since_ns, until_ns)  execlog(since_ns, until_ns)  status()
"""
import base64
import hashlib
import io
import json
import os
import re
import shlex
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time

CTL = "/opt/bc/node_ctl.sh"
SSH_CONFIG = Path(__file__).resolve().parents[1] / "node" / "ssh_config"
SSH_OPTS = ["-F", str(SSH_CONFIG), "-o", "ControlMaster=auto", "-o", "ControlPersist=10m",
            "-o", "ControlPath=/tmp/bc-cm-%C", "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15"]
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
STREAM_CAP = 65536


class InfraFailure(Exception):
    pass


def _check_token(*values):
    for v in values:
        if not SAFE.match(str(v)):
            raise InfraFailure(f"unsafe argument {v!r}")


class SSHNode:
    def __init__(self, host_alias, timeouts=None):
        self.host = host_alias
        self.timeouts = {"setup": 120, "canary": 60, "collect": 90, "teardown": 60,
                         "gwlog": 60, "execlog": 60, "status": 30, **(timeouts or {})}

    def _run(self, cmd, *args, stdin=None, timeout):
        remote = ["sudo", CTL, cmd, *[str(a) for a in args]]
        argv = ["ssh", *SSH_OPTS, self.host, " ".join(shlex.quote(a) for a in remote)]
        if isinstance(stdin, str):
            stdin = stdin.encode()
        try:
            p = subprocess.run(argv, input=stdin if stdin is not None else b"",
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise InfraFailure(f"{self.host} {cmd}: ssh timed out after {timeout}s") from e
        except OSError as e:
            raise InfraFailure(f"{self.host} {cmd}: cannot run ssh: {e}") from e
        if p.returncode != 0:
            tail = p.stderr.decode("utf-8", "replace")[-500:]
            raise InfraFailure(f"{self.host} {cmd}: rc={p.returncode}: {tail}")
        try:
            out = json.loads(p.stdout.decode("utf-8", "replace"))
        except ValueError as e:
            raise InfraFailure(f"{self.host} {cmd}: bad JSON on stdout") from e
        if not isinstance(out, dict):
            raise InfraFailure(f"{self.host} {cmd}: stdout is not a JSON object")
        return out

    def setup(self, ep, slot, tenant_id, api_key, workspace_tar):
        _check_token(ep, slot, tenant_id, api_key)
        return self._run("setup", ep, slot, tenant_id, api_key, stdin=workspace_tar,
                         timeout=self.timeouts["setup"])

    def exec(self, ep, slot, timeout_s, script):
        _check_token(ep, slot, timeout_s)
        return self._run("exec", ep, slot, timeout_s, stdin=script, timeout=int(timeout_s) + 15)

    def canary(self, ep, slot):
        _check_token(ep, slot)
        return self._run("canary", ep, slot, timeout=self.timeouts["canary"])

    def collect(self, ep, slot):
        _check_token(ep, slot)
        return self._run("collect", ep, slot, timeout=self.timeouts["collect"])

    def teardown(self, ep, slot):
        _check_token(ep, slot)
        return self._run("teardown", ep, slot, timeout=self.timeouts["teardown"])

    def gwlog(self, since_ns, until_ns):
        return self._run("gwlog", int(since_ns), int(until_ns), timeout=self.timeouts["gwlog"])

    def execlog(self, since_ns, until_ns):
        return self._run("execlog", int(since_ns), int(until_ns), timeout=self.timeouts["execlog"])

    def status(self):
        return self._run("status", timeout=self.timeouts["status"])


def _cap(data):
    return data[:STREAM_CAP].decode("utf-8", "replace"), len(data)


class FakeNode:
    """Local stand-in: one temp dir per (episode, slot), bash via subprocess.
    No isolation and no gateway; gwlog is always empty. `violations` lists any setup
    that overlapped a different episode on the same node (the fleet must not do that).
    fail_on: {"setup"|"exec"|"canary"|"collect"|"teardown"|"gwlog"|"execlog": True} raises InfraFailure."""

    def __init__(self, name="fake", canary_ok=True, fail_on=None, exec_delay=0.0):
        self.name = name
        self.canary_ok = canary_ok
        self.fail_on = dict(fail_on or {})
        self.exec_delay = exec_delay
        self.violations = []
        self.history = []  # (event, ep, slot) in order, for assertions
        self._dirs = {}
        self._execrows = []
        self._lock = threading.Lock()

    def _maybe_fail(self, cmd):
        if self.fail_on.get(cmd):
            raise InfraFailure(f"{self.name} {cmd}: injected failure")

    def _work(self, ep, slot):
        try:
            return self._dirs[(ep, slot)] + "/work"
        except KeyError:
            raise InfraFailure(f"{self.name}: no container for {ep} slot {slot}")

    def setup(self, ep, slot, tenant_id, api_key, workspace_tar):
        self._maybe_fail("setup")
        with self._lock:
            others = {e for (e, _s) in self._dirs if e != ep}
            if others or (ep, slot) in self._dirs:
                self.violations.append((ep, slot, sorted(others)))
            root = tempfile.mkdtemp(prefix=f"fakenode-{self.name}-{slot}-")
            os.makedirs(root + "/work")
            self._dirs[(ep, slot)] = root
            self.history.append(("setup", ep, slot))
        if workspace_tar:
            with tarfile.open(fileobj=io.BytesIO(workspace_tar), mode="r:gz") as tf:
                tf.extractall(root + "/work", filter="data")
        return {"container": f"bc-{ep}-{slot}", "t_ns": time.time_ns()}

    def exec(self, ep, slot, timeout_s, script):
        self._maybe_fail("exec")
        work = self._work(ep, slot)
        if self.exec_delay:
            time.sleep(self.exec_delay)
        t0 = time.time_ns()
        p = subprocess.Popen(["bash", "-c", script], cwd=work, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
        timed_out = False
        try:
            out, err = p.communicate(timeout=timeout_s)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            out, err = p.communicate()
            timed_out, rc = True, 137
        so, so_n = _cap(out)
        se, se_n = _cap(err)
        res = {"rc": rc, "stdout": so, "stderr": se, "timed_out": timed_out,
               "t_start_ns": t0, "t_end_ns": time.time_ns(),
               "stdout_bytes": so_n, "stderr_bytes": se_n}
        with self._lock:
            self._execrows.append({"episode": ep, "slot": slot, "rc": rc, "timed_out": timed_out,
                                   "t_start_ns": t0, "t_end_ns": res["t_end_ns"],
                                   "script_sha256": hashlib.sha256(script.encode()).hexdigest()})
            self.history.append(("exec", ep, slot))
        return res

    def canary(self, ep, slot):
        self._maybe_fail("canary")
        self._work(ep, slot)
        probes = {"no_internet": self.canary_ok, "no_peer": self.canary_ok, "no_vllm_port": self.canary_ok}
        return {"ok": self.canary_ok, "probes": probes}

    def collect(self, ep, slot):
        self._maybe_fail("collect")
        work = self._work(ep, slot)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(work, arcname=".")
        data = buf.getvalue()
        return {"workspace_tar_b64": base64.b64encode(data).decode(),
                "workspace_sha256": hashlib.sha256(data).hexdigest()}

    def teardown(self, ep, slot):
        self._maybe_fail("teardown")
        with self._lock:
            root = self._dirs.pop((ep, slot), None)
            self.history.append(("teardown", ep, slot))
        if root:
            shutil.rmtree(root, ignore_errors=True)
        return {"removed": root is not None, "t_ns": time.time_ns()}

    def gwlog(self, since_ns, until_ns):
        self._maybe_fail("gwlog")
        return {"rows": []}

    def execlog(self, since_ns, until_ns):
        self._maybe_fail("execlog")
        with self._lock:
            rows = [r for r in self._execrows if since_ns <= r["t_start_ns"] <= until_ns]
        return {"rows": rows}

    def status(self):
        with self._lock:
            return {"vllm_ok": True, "containers": [f"bc-{e}-{s}" for (e, s) in self._dirs],
                    "slot_keys": {}}
