#!/usr/bin/env python3
"""Stop only H's helpers and mark H's remote repository offline."""
import base64
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path.home() / "workspaces/artifactory-lab/h"
state = json.loads((ROOT / "evidence/remote-state.json").read_text())
admin = json.loads((Path.home() / "workspaces/artifactory-lab/a/credentials.json").read_text())["admin"]
config = state["config"]
config["basic"]["offline"] = True
headers = {
    "Authorization": "Basic " + base64.b64encode(("admin:" + admin).encode()).decode(),
    "Content-Type": "application/json", "User-Agent": "h-cleanup",
}
request = urllib.request.Request(
    "http://127.0.0.1:18584/artifactory/ui/admin/repositories",
    data=json.dumps(config).encode(), method="PUT", headers=headers,
)
with urllib.request.urlopen(request, timeout=30) as r:
    status = r.status
results = {"remote_repo_offline_status": status, "network_detaches": []}
for name in ("h-net-sender", "h-net-receiver", "h-net-services"):
    p = subprocess.run(["docker", "network", "disconnect", name, "art-a"],
                       capture_output=True, text=True)
    results["network_detaches"].append({"network": name, "returncode": p.returncode})
p = subprocess.run(["docker", "compose", "-f", str(ROOT / "compose.yaml"), "down"],
                   capture_output=True, text=True)
results["compose_down_returncode"] = p.returncode
results["compose_down_output"] = p.stderr
(ROOT / "evidence/cleanup.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
assert status == 200 and p.returncode == 0
assert all(r["returncode"] == 0 for r in results["network_detaches"])
