#!/usr/bin/env python3
"""Dummy tool-call controller for the d-agentN sandboxes. No LLM.

Runs a scripted list of shell commands in each sandbox with `docker exec` and writes one JSON line per
tool call. This is the shape a real controller would have: the model client lives on the lab host, the
sandbox has no internet, and every tool call goes through run_tool().

Usage: controller.py PLAN.json LOG.jsonl [--ps-sample SECONDS]

PLAN.json: {"agents": {"agent1": [{"cmd": "...", "timeout": 30, "sleep_after": 0.0}, ...], ...},
            "stagger_s": 0.0}

Timestamps. Each call gets two pairs, both wall-clock epoch nanoseconds (the container shares the host
kernel clock, so they are comparable with Artifactory log time):
  t_call_start_ns / t_call_end_ns : taken by the controller around `docker exec` (includes docker overhead)
  t_cmd_start_ns  / t_cmd_end_ns  : taken inside the sandbox by bash's $EPOCHREALTIME (microsecond resolution)
                                    right before and after the command. null if the wrapper's trailer is missing.
"""
import hashlib, json, os, subprocess, sys, threading, time, uuid
from datetime import datetime, timezone

# Runs inside the sandbox. $1 = command, $2 = timeout seconds. Trailer goes to stderr, controller strips it.
WRAPPER = (
    's=$EPOCHREALTIME; timeout -k 2 "$2" bash -c "$1"; rc=$?; e=$EPOCHREALTIME; '
    'printf "\\n@@TIMING %s %s %s\\n" "$s" "$e" "$rc" >&2; exit $rc'
)
TRAILER = b"\n@@TIMING "


def iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def realtime_to_ns(s):
    """'1762576800.123456' -> int ns without going through float."""
    sec, _, frac = s.partition(".")
    return int(sec) * 10**9 + int((frac + "000000000")[:9])


class Log:
    def __init__(self, path):
        self.f = open(path, "a", buffering=1)
        self.lock = threading.Lock()

    def write(self, rec):
        with self.lock:
            self.f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def run_tool(run_id, agent, seq, cmd, timeout, log):
    container = "d-" + agent
    t0 = time.time_ns()
    p = subprocess.run(
        ["docker", "exec", "-i", "-w", "/home/agent", container, "bash", "-c", WRAPPER, "wrapper", cmd, str(timeout)],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout + 15,
    )
    t1 = time.time_ns()
    out, err = p.stdout, p.stderr
    cs = ce = None
    i = err.rfind(TRAILER)
    if i >= 0:
        parts = err[i + len(TRAILER):].split()
        if len(parts) == 3:
            cs, ce = realtime_to_ns(parts[0].decode()), realtime_to_ns(parts[1].decode())
            err = err[:i]
    rec = {
        "v": 1, "kind": "tool_call", "run_id": run_id, "agent": agent, "seq": seq, "cmd": cmd,
        "t_call_start": iso(t0), "t_call_start_ns": t0,
        "t_cmd_start": iso(cs) if cs else None, "t_cmd_start_ns": cs,
        "t_cmd_end": iso(ce) if ce else None, "t_cmd_end_ns": ce,
        "t_call_end": iso(t1), "t_call_end_ns": t1,
        "dur_call_ms": round((t1 - t0) / 1e6, 3),
        "dur_cmd_ms": round((ce - cs) / 1e6, 3) if cs else None,
        "exit_code": p.returncode, "timed_out": p.returncode == 124,
        "stdout_bytes": len(out), "stderr_bytes": len(err),
        "output_sha256": hashlib.sha256(out + err).hexdigest()[:16],
        "stdout_head": out[:200].decode("utf-8", "replace"),
    }
    log.write(rec)
    return rec


def agent_loop(run_id, agent, steps, log, start_delay):
    time.sleep(start_delay)
    for seq, step in enumerate(steps):
        run_tool(run_id, agent, seq, step["cmd"], step.get("timeout", 60), log)
        time.sleep(step.get("sleep_after", 0))


def ps_sampler(run_id, agents, interval, log, stop):
    """Optional monitor: snapshot every sandbox's process list. Covers what tool-call logging cannot see."""
    while not stop.wait(interval):
        for a in agents:
            t = time.time_ns()
            p = subprocess.run(["docker", "top", "d-" + a, "-eo", "pid,etimes,args"], capture_output=True, text=True)
            procs = [l.strip() for l in p.stdout.splitlines()[1:] if l.strip()]
            log.write({"v": 1, "kind": "ps_snapshot", "run_id": run_id, "agent": a, "t": iso(t), "t_ns": t,
                       "procs": procs})


def main():
    plan = json.load(open(sys.argv[1]))
    log = Log(sys.argv[2])
    interval = float(sys.argv[sys.argv.index("--ps-sample") + 1]) if "--ps-sample" in sys.argv else 0
    run_id = uuid.uuid4().hex[:8]
    agents = list(plan["agents"])
    t = time.time_ns()
    log.write({"v": 1, "kind": "run_start", "run_id": run_id, "t": iso(t), "t_ns": t, "agents": agents,
               "plan": os.path.basename(sys.argv[1]), "host": os.uname().nodename})
    stop = threading.Event()
    if interval:
        threading.Thread(target=ps_sampler, args=(run_id, agents, interval, log, stop), daemon=True).start()
    threads = [threading.Thread(target=agent_loop,
                                args=(run_id, a, plan["agents"][a], log, i * plan.get("stagger_s", 0)))
               for i, a in enumerate(agents)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    stop.set()
    t = time.time_ns()
    log.write({"v": 1, "kind": "run_end", "run_id": run_id, "t": iso(t), "t_ns": t})


if __name__ == "__main__":
    main()
