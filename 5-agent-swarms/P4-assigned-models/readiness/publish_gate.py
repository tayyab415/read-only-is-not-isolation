"""Publish the checked public P4 decision on lab2 without dispatching a subject."""
from pathlib import Path
import json
import shlex
import subprocess
import tempfile

import prepare_gate


def main():
    prepare_gate.main()
    p4 = prepare_gate.P4
    decision = prepare_gate.read(p4 / "ALLOW-SUBJECTS.json")
    precheck = """
from pathlib import Path
import hashlib,json,sys
p=Path.home()/'workspaces/artifactory-lab/next/P4-models'
decision=json.loads(sys.stdin.readline())
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
final=p/'ALLOW-SUBJECTS.json'
if final.exists() and json.loads(final.read_text())!=decision:
    raise RuntimeError('Preserve the different remote subject decision')
for name,expected in decision['evidence_sha256'].items():
    path=(p/'gate-evidence'/name).resolve()
    if (p/'gate-evidence').resolve() not in path.parents:
        raise RuntimeError('Unexpected public gate path')
    if path.exists() and sha(path)!=expected:
        raise RuntimeError('Preserve different remote gate evidence')
"""
    # The JSON decision travels on stdin; only the quoted script enters the shell.
    subprocess.run(["ssh", "-o", "BatchMode=yes", "lab2", "python3", "-c",
                    shlex.quote(precheck)],
                   input=json.dumps(decision) + "\n", text=True, check=True)
    with tempfile.TemporaryDirectory(prefix="p4-public-gate-") as temp:
        filelist = Path(temp) / "files.txt"
        filelist.write_text("\n".join(sorted(decision["evidence_sha256"])) + "\n")
        subprocess.run(["ssh", "-o", "BatchMode=yes", "lab2",
                        "mkdir -p ~/workspaces/artifactory-lab/next/P4-models/gate-evidence"],
                       check=True)
        subprocess.run(["rsync", "-a", "--files-from=" + str(filelist),
                        str(p4 / "gate-evidence") + "/",
                        "lab2:workspaces/artifactory-lab/next/P4-models/gate-evidence/"],
                       check=True)
    subprocess.run(["rsync", "-a", str(p4 / "ALLOW-SUBJECTS.json"),
                    "lab2:workspaces/artifactory-lab/next/P4-models/ALLOW-SUBJECTS.pending.json"],
                   check=True)
    script = """
from pathlib import Path
import hashlib,sys
p=Path.home()/'workspaces/artifactory-lab/next/P4-models'
final=p/'ALLOW-SUBJECTS.json'; pending=p/'ALLOW-SUBJECTS.pending.json'
if final.exists() and hashlib.sha256(final.read_bytes()).digest()!=hashlib.sha256(pending.read_bytes()).digest():
    raise RuntimeError('Preserve the different remote subject decision')
sys.path.insert(0,str(p))
import gate
gate.sources()
pending.replace(final)
gate.check()
print('Public evidence and paid-study decision verified on lab2; no subject dispatched')
"""
    subprocess.run(["ssh", "-o", "BatchMode=yes", "lab2", "python3", "-"],
                   input=script, text=True, check=True)


if __name__ == "__main__":
    main()
