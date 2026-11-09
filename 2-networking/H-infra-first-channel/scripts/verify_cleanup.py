#!/usr/bin/env python3
"""Read back the H repository flag and verify only H's runtime is absent."""
import base64
import datetime
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path.home() / "workspaces/artifactory-lab"
OUT = ROOT / "h/evidence"
state = json.loads((OUT / "remote-state.json").read_text())
admin = json.loads((ROOT / "a/credentials.json").read_text())["admin"]
request = urllib.request.Request(
    "http://127.0.0.1:18584/artifactory/ui/admin/repositories/remote/" + state["repo"],
    headers={"Authorization": "Basic " + base64.b64encode(("admin:" + admin).encode()).decode(),
             "User-Agent": "h-cleanup-readback"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    config = json.load(response)
containers = subprocess.check_output(
    ["docker", "ps", "-a", "--format", "{{.Names}}"], text=True).splitlines()
networks = subprocess.check_output(
    ["docker", "network", "ls", "--format", "{{.Name}}"], text=True).splitlines()
result = {
    "checked_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "remote_repo": state["repo"],
    "remote_offline": config["basic"]["offline"],
    "remaining_h_containers": sorted(set(containers) & {
        "h-sender", "h-receiver", "h-upstream", "h-target"}),
    "remaining_h_networks": sorted(set(networks) & {
        "h-net-sender", "h-net-receiver", "h-net-services"}),
    "preexisting_a_c_containers_present": sorted(set(containers) & {
        "art-a", "art-a-db", "art-c", "art-c-db"}),
}
(OUT / "cleanup-readback.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
assert result["remote_offline"] is True
assert not result["remaining_h_containers"]
assert not result["remaining_h_networks"]
