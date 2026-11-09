#!/usr/bin/env python3
"""C feasibility: run tasks/scripted_c.py inside a real sandbox for each workload, time it,
and check the output with tasks/check_c.py. Both workloads must finish within 240 s.
Usage: run_feasibility.py --node n01 --out validation/feasibility.json
"""
import argparse, base64, gzip, io, json, subprocess, sys, tarfile, time, uuid
from pathlib import Path

BC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BC / "tasks"))
import make_fixtures, check_c  # noqa: E402

SSH_CONFIG = BC / "node" / "ssh_config"


def ctl(node, *args, data=b"", timeout=120):
    p = subprocess.run(["ssh", "-F", str(SSH_CONFIG), f"bc-{node}", "sudo", "/opt/bc/node_ctl.sh", *map(str, args)],
                       input=data, capture_output=True, timeout=timeout)
    res = json.loads(p.stdout.decode().strip().splitlines()[-1])
    if p.returncode:
        raise RuntimeError(f"{args[0]} failed: {res}")
    return res


def with_solver(tar_gz):
    src = tarfile.open(fileobj=io.BytesIO(gzip.decompress(tar_gz)))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as dst:
        for m in src.getmembers():
            dst.addfile(m, src.extractfile(m) if m.isfile() else None)
        data = (BC / "tasks" / "scripted_c.py").read_bytes()
        ti = tarfile.TarInfo("scripted_c.py"); ti.size = len(data); ti.mode = 0o755
        dst.addfile(ti, io.BytesIO(data))
    return gzip.compress(buf.getvalue())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    results = []
    for workload in ("modest", "pressure"):
        tar, info = make_fixtures.make_workspace("C", "A", workload, 424242 + len(results))
        ep = f"val-feas-{workload}-{uuid.uuid4().hex[:6]}"
        ctl(a.node, "setup", ep, 1, f"{ep}-A", uuid.uuid4().hex, data=with_solver(tar))
        try:
            t0 = time.time()
            ctl(a.node, "exec", ep, 1, 10, data=b"nohup python3 scripted_c.py > /tmp/solver.log 2>&1 & echo $! > /tmp/solver.pid")
            done = False
            while time.time() - t0 < 300:
                r = ctl(a.node, "exec", ep, 1, 10, data=b"kill -0 $(cat /tmp/solver.pid) 2>/dev/null && echo running || echo done")
                if r["stdout"].strip() == "done":
                    done = True
                    break
                time.sleep(3)
            elapsed = time.time() - t0
            log = ctl(a.node, "exec", ep, 1, 10, data=b"tail -5 /tmp/solver.log")["stdout"]
            ws = base64.b64decode(ctl(a.node, "collect", ep, 1)["workspace_tar_b64"])
            chk = check_c.check(ws, info)
        finally:
            ctl(a.node, "teardown", ep, 1)
        res = {"workload": workload, "n_records": info["n_records"], "finished": done,
               "elapsed_s_upper": round(elapsed, 1), "within_240s": done and elapsed <= 240,
               "complete": chk.get("complete"), "format_ok": chk.get("format_ok"),
               "accuracy": chk.get("accuracy"), "solver_log_tail": log}
        results.append(res)
        print(json.dumps(res), flush=True)
    Path(a.out).write_text(json.dumps({"node": a.node, "results": results,
                                       "note": "elapsed includes ~3 s polling granularity and ssh overhead"}, indent=1) + "\n")


if __name__ == "__main__":
    main()
