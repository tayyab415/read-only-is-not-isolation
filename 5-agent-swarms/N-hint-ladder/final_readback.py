#!/usr/bin/env python3
"""Read-only lab host runtime check; no credentials or container mutation."""
import datetime as dt
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import json, subprocess
def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise RuntimeError("readback failed: " + args[0])
    return p.stdout
containers = [json.loads(line) for line in run(
    ["docker", "ps", "-a", "--format", "{{json .}}"]).splitlines()]
networks = [json.loads(line) for line in run(
    ["docker", "network", "ls", "--format", "{{json .}}"]).splitlines()]
art = json.loads(run(["docker", "inspect", "art-a"]))[0]
print(json.dumps({
    "containers": containers, "networks": networks,
    "art_a_networks": sorted(art["NetworkSettings"]["Networks"]),
    "art_a_image": art["Config"]["Image"],
    "art_a_image_id": art["Image"],
    "art_a_state": {
        "running": art["State"]["Running"], "status": art["State"]["Status"],
        "health": (art["State"].get("Health") or {}).get("Status"),
    },
    "memory": run(["free", "-b"]),
    "owned_containers": [r["Names"] for r in containers if r["Names"].startswith("n-")],
    "owned_networks": [r["Name"] for r in networks if r["Name"].startswith("n-")],
    "owned_art_a_attachments": [n for n in art["NetworkSettings"]["Networks"] if n.startswith("n-")],
}))
'''


def main():
    result = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "lab-host", "python3 -"],
                            input=REMOTE, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"lab host readback failed, rc={result.returncode}")
    report = json.loads(result.stdout)
    report["checked_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["owned_runtime_absent"] = not any(report[k] for k in [
        "owned_containers", "owned_networks", "owned_art_a_attachments"])
    root = Path(__file__).resolve().parent
    (root / "data" / "campaign-final-hub-readback.json").write_text(
        json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in [
        "checked_utc", "owned_runtime_absent", "owned_containers", "owned_networks",
        "owned_art_a_attachments", "art_a_image"]}))


if __name__ == "__main__":
    main()
