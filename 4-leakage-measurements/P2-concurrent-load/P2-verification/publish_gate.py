"""Publish P2b's actual evidence gate after complete independent verification."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
P2B = HERE.parent / "P2b-attribution"
sys.path.insert(0, str(P2B))
import p2b


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p2b.check_sources()
    state = json.loads((P2 / "controller-status.json").read_text())
    if state["stage"] != "results_ready_for_lead":
        raise RuntimeError("The sole P2 controller has not completed the held sequence")
    frozen = json.loads((P2 / "FROZEN.json").read_text())
    data, output = P2 / frozen["data"], P2 / frozen["out"]
    verification = json.loads((HERE / "RESULT.json").read_text())
    if verification.get("passed") is not True or verification.get("primary_aa_null_passed") is not True \
            or verification["holdout_results_sha256"] != sha(output / "holdout-results.json") \
            or verification["verification_freeze_sha256"] != sha(HERE / "VERIFICATION-FROZEN.json"):
        raise RuntimeError("Passing current independent verification and A/A null required")
    files = {
        "holdout-audit-results.json": data / "holdout-audit-results.json",
        "holdout-results.json": output / "holdout-results.json",
        "FROZEN.json": P2 / "FROZEN.json",
        **{f"holdout-seal-{arm}.json": data / arm / "holdout-sealed.json"
           for arm in p2b.plan.CONFIGURATIONS},
    }
    evidence = P2B / "gate-evidence"
    evidence.mkdir(exist_ok=True)
    for name, source in files.items():
        target = evidence / name
        if target.exists() and sha(target) != sha(source):
            raise RuntimeError("Preserve contradictory prior gate evidence")
        if not target.exists():
            shutil.copy2(source, target)
    gate = {
        "p2_holdout_audit_passed": True, "p2_aa_null_passed": True,
        "p2_source_freeze_sha256": sha(P2 / "SOURCES-FROZEN.json"),
        "evidence_sha256": {name: sha(evidence / name) for name in files},
        "independent_verification_sha256": sha(HERE / "RESULT.json"),
    }
    path = P2B / "P2-GATE.json"
    if path.exists():
        if json.loads(path.read_text()) != gate:
            raise RuntimeError("Preserve the different prior P2 decision")
    else:
        with path.open("x") as stream:
            json.dump(gate, stream, indent=2)
    p2b.p2_gate()
    subprocess.run(["ssh", "-o", "BatchMode=yes", "labhost",
                    "mkdir -p ~/workspaces/artifactory-lab/next/P2b-attribution/gate-evidence"], check=True)
    subprocess.run(["rsync", "-a", str(evidence) + "/",
                    "labhost:workspaces/artifactory-lab/next/P2b-attribution/gate-evidence/"], check=True)
    subprocess.run(["rsync", "-a", str(path),
                    "labhost:workspaces/artifactory-lab/next/P2b-attribution/P2-GATE.pending.json"], check=True)
    script = """
from pathlib import Path
import sys,hashlib
p=Path.home()/'workspaces/artifactory-lab/next/P2b-attribution'
pending=p/'P2-GATE.pending.json'; final=p/'P2-GATE.json'
if final.exists() and hashlib.sha256(final.read_bytes()).digest()!=hashlib.sha256(pending.read_bytes()).digest():
    raise RuntimeError('Preserve the different remote P2 decision')
pending.replace(final)
sys.path.insert(0,str(p))
import p2b
p2b.check_sources(); p2b.p2_finished(); p2b.p2_gate()
print('Actual P2 evidence gate verified on the quiet labhost')
"""
    subprocess.run(["ssh", "-o", "BatchMode=yes", "labhost", "python3", "-"],
                   input=script, text=True, check=True)


if __name__ == "__main__":
    main()
