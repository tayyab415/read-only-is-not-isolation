#!/usr/bin/env python3
"""Read back every running BC node: code hashes vs local files, services, firewall, GPU id,
actor image, vLLM version. Fails if any node differs or two nodes report the same GPU UUID.
Usage: fleet_readback.py [--out validation/fleet-readback.json]
"""
import argparse, hashlib, json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BC = Path(__file__).resolve().parents[1]
NODE = BC / "node"
FILES = ["gateway_bc.py", "node_ctl.py", "node_ctl.sh", "firewall.sh"]
REMOTE = r'''
set -u
cd /opt/bc
echo "SHA $(sha256sum gateway_bc.py node_ctl.py node_ctl.sh firewall.sh | tr '\n' ';')"
echo "READY $(cat READY 2>/dev/null || cat PROVISIONED 2>/dev/null || echo none)"
echo "ACTIVE $(systemctl is-active bc-vllm bc-gw1 bc-gw2 bc-firewall | tr '\n' ' ')"
echo "FW $(sudo iptables -S INPUT | grep -c 10.201) $(sudo iptables -S DOCKER-USER | grep -c 10.201)"
echo "GPU $(nvidia-smi --query-gpu=uuid --format=csv,noheader)"
echo "IMG $(cat actor-image-id)"
echo "VLLM $(sudo /opt/bc/venv/bin/python -c 'import importlib.metadata as m; print(m.version("vllm"))' 2>/dev/null)"
echo "STATUS $(sudo /opt/bc/node_ctl.sh status)"
'''


def read(node):
    p = subprocess.run(["ssh", "-F", str(NODE / "ssh_config"), "-o", "ConnectTimeout=10", node["ssh"], REMOTE],
                       capture_output=True, text=True, timeout=90)
    out = {"name": node["name"], "instance_id": node["instance_id"], "az": node.get("availability_zone")}
    for line in p.stdout.splitlines():
        key, _, val = line.partition(" ")
        out[key.lower()] = val.strip()
    local = {f: hashlib.sha256((NODE / f).read_bytes()).hexdigest() for f in FILES}
    remote = {}
    for part in out.get("sha", "").split(";"):
        if part.strip():
            h, f = part.split()
            remote[f] = h
    out["code_match"] = remote == local
    status = json.loads(out.get("status") or "{}")
    out["ok"] = (out["code_match"] and out.get("active") == "active active active active"
                 and out.get("fw") == "3 1" and status.get("vllm_ok") is True and not status.get("containers")
                 and out.get("vllm") == "0.33.0" and out.get("ready", "none") != "none")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BC / "validation" / "fleet-readback.json"))
    a = ap.parse_args()
    nodes = [n for n in json.loads((NODE / "nodes.json").read_text()) if n.get("state") == "running"]
    with ThreadPoolExecutor(16) as pool:
        rows = list(pool.map(read, nodes))
    gpus = [r.get("gpu") for r in rows]
    imgs = sorted({r.get("img") for r in rows})
    res = {"nodes": rows, "all_ok": all(r["ok"] for r in rows), "distinct_gpu_uuids": len(set(gpus)) == len(gpus),
           "actor_image_ids": imgs}
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    for r in rows:
        print(r["name"], "OK" if r["ok"] else "NOT OK", r.get("az"), (r.get("gpu") or "")[-12:], r.get("ready"),
              "" if r["code_match"] else "CODE MISMATCH", r.get("active") if r.get("active") != "active active active active" else "")
    print(json.dumps({k: res[k] for k in ("all_ok", "distinct_gpu_uuids", "actor_image_ids")}))
    sys.exit(0 if res["all_ok"] and res["distinct_gpu_uuids"] else 1)


if __name__ == "__main__":
    main()
