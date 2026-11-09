#!/usr/bin/env python3
"""BC node control. Run as root via /opt/bc/node_ctl.sh. Prints exactly one JSON object.

Commands (see ../SPEC.md): setup, exec, canary, collect, teardown, gwlog, execlog, status.
The actor container sits on docker network bcnet<slot>; host iptables (installed by
provision.sh) allow it to reach only 10.201.<slot>.1:9000, the slot gateway.
"""
import base64, hashlib, json, os, subprocess, sys, threading, time, urllib.request

ROOT = "/opt/bc"
IMAGE = "bc-actor:1"
CAP = 65536
EXEC_LOG = f"{ROOT}/logs/exec.jsonl"
CTL_LOG = f"{ROOT}/logs/ctl.jsonl"


def out(obj, code=0):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
    sys.exit(code)


def append(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def name(ep, slot):
    return f"bc-{ep}-{slot}"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def write_key(slot, obj):
    path = f"{ROOT}/keys/slot{slot}.json"
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def capped_reader(stream, sink):
    total, kept = 0, bytearray()
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        total += len(chunk)
        if len(kept) < CAP:
            kept.extend(chunk[:CAP - len(kept)])
    sink["data"], sink["bytes"] = bytes(kept).decode("utf-8", "replace"), total


def cmd_setup(ep, slot, tenant, key):
    slot = int(slot)
    c = name(ep, slot)
    tar = sys.stdin.buffer.read()
    if run(["docker", "inspect", c]).returncode == 0:
        out({"error": f"{c} exists"}, 1)
    owner_now = json.load(open(f"{ROOT}/keys/slot{slot}.json")) if os.path.exists(f"{ROOT}/keys/slot{slot}.json") else {}
    if owner_now:
        out({"error": f"slot {slot} busy", "owner_episode": owner_now.get("episode")}, 1)
    r = run(["docker", "create", "--init", "--name", c, "--hostname", "sandbox", "--network", f"bcnet{slot}",
             "--add-host", f"modelsvc:10.201.{slot}.1", "--cpus", "1.0", "--memory", "1g",
             "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "-e", "MODEL_BASE_URL=http://modelsvc:9000/v1", "-e", f"MODEL_API_KEY={key}",
             "-e", "MODEL_NAME=qwen2.5-1.5b-instruct", "--label", f"bc.episode={ep}",
             "--label", f"bc.slot={slot}", IMAGE, "sleep", "infinity"])
    if r.returncode:
        out({"error": "docker create failed", "stderr": r.stderr[-2000:]}, 1)
    p = subprocess.run(["docker", "cp", "-a", "-", f"{c}:/work"], input=actor_owned_tar(tar), capture_output=True)
    if p.returncode:
        run(["docker", "rm", "-f", c])
        out({"error": "docker cp failed", "stderr": p.stderr.decode()[-2000:]}, 1)
    r = run(["docker", "start", c])
    if r.returncode:
        run(["docker", "rm", "-f", c])
        out({"error": "docker start failed", "stderr": r.stderr[-2000:]}, 1)
    write_key(slot, {"key": key, "tenant": tenant, "episode": ep})
    reset = reset_prefix_cache()
    t = time.time_ns()
    append(CTL_LOG, {"cmd": "setup", "ep": ep, "slot": slot, "tenant": tenant, "container": c,
                     "key_sha256_12": hashlib.sha256(key.encode()).hexdigest()[:12],
                     "workspace_sha256": hashlib.sha256(tar).hexdigest(), "reset": reset, "t_ns": t})
    out({"container": c, "t_ns": t, "prefix_cache_reset": reset})


def actor_owned_tar(gz):
    """Re-pack the workspace with uid/gid 1500 (actor); docker cp -a keeps ownership."""
    import gzip, io, tarfile
    src = tarfile.open(fileobj=io.BytesIO(gzip.decompress(gz)))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as dst:
        for m in src.getmembers():
            if not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in m.name.split("/"):
                raise ValueError(f"unsafe workspace member {m.name}")
            m.uid = m.gid = 1500
            m.uname = m.gname = "actor"
            dst.addfile(m, src.extractfile(m) if m.isfile() else None)
    return buf.getvalue()


def reset_prefix_cache():
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/reset_prefix_cache", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def cmd_exec(ep, slot, timeout_s):
    c = name(ep, int(slot))
    script = sys.stdin.read()
    timeout_s = max(1, min(int(timeout_s), 60))
    t0 = time.time_ns()
    p = subprocess.Popen(["docker", "exec", "-u", "actor", "-w", "/work", c, "timeout", "-s", "KILL",
                          str(timeout_s), "bash", "-c", script],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    so, se = {}, {}
    ts = [threading.Thread(target=capped_reader, args=(p.stdout, so)),
          threading.Thread(target=capped_reader, args=(p.stderr, se))]
    for t in ts:
        t.start()
    timed_out = False
    try:
        rc = p.wait(timeout=timeout_s + 3)
    except subprocess.TimeoutExpired:
        # A background job holding stdout open keeps docker exec attached; detach.
        p.kill()
        rc = p.wait()
        timed_out = True
    for t in ts:
        t.join(timeout=3)
    t1 = time.time_ns()
    if rc == 137 or rc == 124:
        timed_out = True
    res = {"rc": rc, "stdout": so.get("data", ""), "stderr": se.get("data", ""), "timed_out": timed_out,
           "t_start_ns": t0, "t_end_ns": t1, "stdout_bytes": so.get("bytes", 0), "stderr_bytes": se.get("bytes", 0)}
    append(EXEC_LOG, {"ep": ep, "slot": int(slot), "container": c, "t_start_ns": t0, "t_end_ns": t1, "rc": rc,
                      "timed_out": timed_out, "timeout_s": timeout_s,
                      "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
                      "stdout_sha256": hashlib.sha256(res["stdout"].encode()).hexdigest(),
                      "stderr_sha256": hashlib.sha256(res["stderr"].encode()).hexdigest(),
                      "stdout_bytes": res["stdout_bytes"], "stderr_bytes": res["stderr_bytes"]})
    out(res)


CANARY = r'''
import json, socket, sys
slot = int(sys.argv[1]); other = 2 if slot == 1 else 1
def tcp(host, port):
    s = socket.socket(); s.settimeout(1.5)
    try:
        s.connect((host, port)); return True
    except Exception:
        return False
    finally:
        s.close()
def dns(name):
    try:
        socket.getaddrinfo(name, 443); return True
    except Exception:
        return False
reach = {
  "own_gateway": tcp("modelsvc", 9000),
  "vllm_on_bridge": tcp(f"10.201.{slot}.1", 8000),
  "ssh_on_bridge": tcp(f"10.201.{slot}.1", 22),
  "other_slot_gateway": tcp(f"10.201.{other}.1", 9000),
  "docker0_ssh": tcp("172.17.0.1", 22),
  "imds": tcp("169.254.169.254", 80),
  "internet_ip": tcp("1.1.1.1", 443),
  "dns_external": dns("huggingface.co"),
  "other_slot_container_2": tcp(f"10.201.{other}.2", 22),
}
print(json.dumps(reach))
'''


def cmd_canary(ep, slot):
    c = name(ep, int(slot))
    r = run(["docker", "exec", "-u", "root", "-w", "/", c, "python3", "-c", CANARY, str(slot)], timeout=60)
    try:
        reach = json.loads(r.stdout)
    except Exception:
        out({"ok": False, "error": "canary did not run", "stderr": r.stderr[-2000:]}, 1)
    ok = reach.pop("own_gateway") and not any(reach.values())
    probes = {"own_gateway_reachable": True, **{k: (not v) for k, v in reach.items()}}
    res = {"ok": bool(ok), "probes": probes, "t_ns": time.time_ns()}
    append(CTL_LOG, {"cmd": "canary", "ep": ep, "slot": int(slot), **res})
    out(res)


def cmd_collect(ep, slot):
    import gzip
    c = name(ep, int(slot))
    p = subprocess.run(["docker", "cp", f"{c}:/work", "-"], capture_output=True, timeout=120)
    if p.returncode:
        out({"error": "collect failed", "stderr": p.stderr.decode()[-2000:]}, 1)
    gz = gzip.compress(p.stdout, mtime=0)
    out({"workspace_tar_b64": base64.b64encode(gz).decode(), "workspace_sha256": hashlib.sha256(gz).hexdigest(),
         "tar_bytes": len(p.stdout)})


def cmd_teardown(ep, slot):
    slot = int(slot)
    c = name(ep, slot)
    r = run(["docker", "rm", "-f", c])
    path = f"{ROOT}/keys/slot{slot}.json"
    owner = json.load(open(path)) if os.path.exists(path) else {}
    if not owner or owner.get("episode") == ep:
        write_key(slot, {})
    t = time.time_ns()
    gone = run(["docker", "inspect", c]).returncode != 0
    append(CTL_LOG, {"cmd": "teardown", "ep": ep, "slot": slot, "removed": gone, "t_ns": t})
    out({"removed": gone, "t_ns": t, "rm_rc": r.returncode})


def window_rows(paths, lo, hi, key):
    rows = []
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    if lo <= row.get(key, 0) <= hi:
                        rows.append(row)
    return sorted(rows, key=lambda r: r.get(key, 0))


def cmd_gwlog(lo, hi):
    out({"rows": window_rows([f"{ROOT}/logs/gw-slot1.jsonl", f"{ROOT}/logs/gw-slot2.jsonl"], int(lo), int(hi), "t_in")})


def cmd_execlog(lo, hi):
    out({"rows": window_rows([EXEC_LOG], int(lo), int(hi), "t_start_ns")})


def cmd_status():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    cs = run(["docker", "ps", "-a", "--filter", "label=bc.episode", "--format", "{{.Names}}"]).stdout.split()
    keys = {}
    for s in (1, 2):
        path = f"{ROOT}/keys/slot{s}.json"
        o = json.load(open(path)) if os.path.exists(path) else {}
        keys[str(s)] = o.get("episode")
    out({"vllm_ok": ok, "containers": cs, "slot_keys": keys, "t_ns": time.time_ns()})


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    try:
        {"setup": cmd_setup, "exec": cmd_exec, "canary": cmd_canary, "collect": cmd_collect,
         "teardown": cmd_teardown, "gwlog": cmd_gwlog, "execlog": cmd_execlog, "status": cmd_status}[cmd](*rest)
    except SystemExit:
        raise
    except Exception as e:
        out({"error": f"{type(e).__name__}: {e}"}, 1)
