#!/usr/bin/env python3
"""Persistent in-sandbox clients and incremental server-log collection for P1.

Provisioning, cleanup and the gateway/upstream/dummy servers come from the
builder's service.py, unchanged. This module replaces its per-request
`docker exec` with one long-lived client process per sandbox, so a trial's
probe battery runs inside the sandbox without controller round trips.
"""
import json
import select
import subprocess

import service

# Runs inside the sandbox. Credentials arrive once on stdin and never in argv.
AGENT = r'''
import base64, concurrent.futures, hashlib, http.client, json, sys, time
cfg = json.loads(sys.stdin.readline())
auth = "Basic " + base64.b64encode((cfg.pop("user") + ":" + cfg.pop("password")).encode()).decode()
TEXT = ("json", "html", "text", "xml")
def do(r):
    host, port = (cfg["dummy_host"], 8000) if r.get("target") == "dummy" else (cfg["host"], cfg["port"])
    headers = {"Authorization": auth, "User-Agent": r["marker"]}
    headers.update(r.get("headers") or {})
    out = {"marker": r["marker"]}
    conn = http.client.HTTPConnection(host, port, timeout=30)
    t0 = time.time_ns()
    m0 = time.monotonic_ns()
    try:
        conn.request(r["method"], r["path"], headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        m1 = time.monotonic_ns()
        ctype = resp.getheader("Content-Type") or ""
        out.update(status=resp.status, headers=resp.getheaders(),
                   body_sha256=hashlib.sha256(body).hexdigest(), body_length=len(body),
                   body_text=body.decode("utf-8", "replace")
                   if len(body) <= 65536 and any(t in ctype for t in TEXT) else None)
    except Exception as e:
        m1 = time.monotonic_ns()
        out.update(status=None, error=type(e).__name__)
    finally:
        conn.close()
    out.update(start_utc_ns=t0, end_utc_ns=time.time_ns(), elapsed_ms=(m1 - m0) / 1e6)
    return out
pool = concurrent.futures.ThreadPoolExecutor(max_workers=12)
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if msg["op"] == "exit":
        break
    if msg["op"] == "seq":
        res = [do(r) for r in msg["reqs"]]
    else:
        res = list(pool.map(do, msg["reqs"]))
    print(json.dumps(res), flush=True)
'''


class Client:
    """One persistent HTTP client process inside one sandbox."""

    def __init__(self, run_id, actor):
        state = service.load_state(run_id)
        creds = service.load_credentials(run_id)
        self.run_id, self.actor, self.state = run_id, actor, state
        self._secret = creds[state["users"][actor]]
        self.proc = subprocess.Popen(
            ["docker", "exec", "-i", state["containers"][actor], "python3", "-u", "-c", AGENT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        cfg = {"user": state["users"][actor], "password": self._secret,
               "host": state["containers"]["gateway"], "port": 8000,
               "dummy_host": state["containers"].get(f"dummy_{actor}")}
        self.proc.stdin.write(json.dumps(cfg) + "\n")
        self.proc.stdin.flush()
        if not self._read(60).get("ready"):
            raise RuntimeError(f"{actor} client did not start")

    def _read(self, timeout):
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            raise RuntimeError(f"{self.actor} client timed out")
        line = self.proc.stdout.readline()
        if not line:
            err = service.redact(self.proc.stderr.read()[-500:], [self._secret])
            raise RuntimeError(f"{self.actor} client exited: {err}")
        return json.loads(line)

    def _send(self, op, reqs, timeout):
        for r in reqs:
            if r.get("target", "gateway") == "gateway":
                service.allowed_path(r["path"], self.state, self.actor)
        wire = [{**r, "path": ("/artifactory" + r["path"]) if r.get("target", "gateway") == "gateway" else r["path"]}
                for r in reqs]
        self.proc.stdin.write(json.dumps({"op": op, "reqs": wire}) + "\n")
        self.proc.stdin.flush()
        results = self._read(timeout)
        return [{**r, **res, "actor": self.actor} for r, res in zip(reqs, results)]

    def seq(self, reqs, timeout=180):
        return self._send("seq", reqs, timeout)

    def par(self, reqs, timeout=60):
        return self._send("par", reqs, timeout)

    def close(self):
        try:
            self.proc.stdin.write(json.dumps({"op": "exit"}) + "\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


LOG_DIR = "/var/opt/jfrog/artifactory/log"


def server_log_lines(run_id):
    """Return this run's lines from Artifactory's request and router logs, including rolled archives."""
    needle = f"p1/{run_id}/"
    script = (
        f"cd {LOG_DIR} && for f in artifactory-request.log router-request.log; do cat $f; done; "
        "for f in archived/artifactory-request.*.log.gz archived/router-request.*.log.gz; do "
        "[ -f \"$f\" ] && gzip -dc \"$f\"; done; true")
    out = service.run_ok(["docker", "exec", "art-a", "sh", "-c", script])
    return [line for line in out.splitlines() if needle in line]
