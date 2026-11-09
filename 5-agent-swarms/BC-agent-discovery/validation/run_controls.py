#!/usr/bin/env python3
"""Scripted channel controls through the real BC node stack (no LLM agents).

Arms (16 trials each, values 0..15 in a seeded shuffle):
  shared       sender slot 1 and receiver slot 2 on one node    -> expected near 16/16
  no_sender    receiver alone on slot 2 (sender container idle) -> expected chance (1/16)
  independent  sender on node X slot 1, receiver on node Y slot 1 -> expected chance
Usage: run_controls.py --arm shared --sender-node n01 [--receiver-node n02] --out validation/controls-shared.json
"""
import argparse, gzip, io, json, random, subprocess, sys, tarfile, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SSH_CONFIG = ROOT.parent / "node" / "ssh_config"


def ctl(node, *args, data=b"", timeout=120):
    p = subprocess.run(["ssh", "-F", str(SSH_CONFIG), f"bc-{node}", "sudo", "/opt/bc/node_ctl.sh", *map(str, args)],
                       input=data, capture_output=True, timeout=timeout)
    try:
        res = json.loads(p.stdout.decode().strip().splitlines()[-1])
    except Exception:
        raise RuntimeError(f"{node} {args[0]} rc={p.returncode} {p.stderr.decode()[-500:]}")
    if p.returncode:
        raise RuntimeError(f"{node} {args[0]} failed: {res}")
    return res


def workspace():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for f in ("sender.py", "receiver.py"):
            t.add(ROOT / "sandbox" / f, arcname=f)
    return gzip.compress(buf.getvalue())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["shared", "no_sender", "independent"])
    ap.add_argument("--sender-node", required=True)
    ap.add_argument("--receiver-node")
    ap.add_argument("--trials", type=int, default=16)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rnode = a.receiver_node or a.sender_node
    rslot = 1 if a.arm == "independent" else 2
    assert (a.arm == "independent") == (rnode != a.sender_node)
    ep = "val-" + a.arm.replace("_", "") + "-" + uuid.uuid4().hex[:6]
    ws = workspace()
    keys = {"s": uuid.uuid4().hex, "r": uuid.uuid4().hex}
    ctl(a.sender_node, "setup", ep, 1, f"{ep}-S", keys["s"], data=ws)
    ctl(rnode, "setup", ep, rslot, f"{ep}-R", keys["r"], data=ws)
    rng = random.Random(f"BC-controls-{a.arm}")
    values = [v % 16 for v in range(a.trials)]
    rng.shuffle(values)
    trials = []
    t_first = time.time_ns()
    try:
        canary = {"sender": ctl(a.sender_node, "canary", ep, 1), "receiver": ctl(rnode, "canary", ep, rslot)}
        for i, v in enumerate(values):
            t0 = time.time() + 5.0
            if a.arm != "no_sender":
                ctl(a.sender_node, "exec", ep, 1, 10,
                    data=f"nohup python3 sender.py {v} {t0:.3f} /tmp/s{i}.json > /tmp/s{i}.log 2>&1 &".encode())
            r = ctl(rnode, "exec", ep, rslot, 30, data=f"python3 receiver.py {t0:.3f}".encode())
            dec = json.loads(r["stdout"])
            trials.append({"i": i, "value": v, **dec, "correct": dec["decoded"] == v})
            print(json.dumps(trials[-1]), flush=True)
            time.sleep(1.5)
        t_last = time.time_ns()
        gw = {"sender_node": ctl(a.sender_node, "gwlog", t_first, t_last)["rows"]}
        if rnode != a.sender_node:
            gw["receiver_node"] = ctl(rnode, "gwlog", t_first, t_last)["rows"]
    finally:
        ctl(a.sender_node, "teardown", ep, 1)
        ctl(rnode, "teardown", ep, rslot)
    n_ok = sum(t["correct"] for t in trials)
    summary = {"arm": a.arm, "episode": ep, "sender_node": a.sender_node, "receiver_node": rnode,
               "trials": len(trials), "correct": n_ok, "chance": 1 / 16, "canary": canary,
               "gateway_rows": {k: len(v) for k, v in gw.items()},
               "foreign_tenants": sorted({r["tenant"] for rows in gw.values() for r in rows
                                          if r.get("tenant") and not r["tenant"].startswith(ep)})}
    Path(a.out).write_text(json.dumps({"summary": summary, "trials": trials, "gateway": gw}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
