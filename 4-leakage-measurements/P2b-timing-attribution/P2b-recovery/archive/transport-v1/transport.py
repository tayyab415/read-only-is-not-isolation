"""Retrieve a complete current P2b public split and replay its native audit."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent / "P2b-attribution"
sys.path.insert(0, str(STUDY))
import p2b
import audit
import analyze


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("smoke", "calibration", "holdout"))
    split = parser.parse_args().split
    result_path = HERE / f"TRANSPORT-{split}.json"
    if result_path.exists():
        raise RuntimeError("Preserve the existing transport result")
    p2b.check_sources()
    model = STUDY / "DECODERS-FROZEN.json"
    if split == "holdout" and not model.exists():
        raise RuntimeError("Retrieve and review the prior calibration/model freeze first")
    config = {
        "split": split, "size": p2b.SIZES[split],
        "source_manifest_sha256": sha(STUDY / "SOURCES-SHA256.txt"),
        "prior_model_sha256": sha(model) if split == "holdout" else None,
    }
    script = """
from pathlib import Path
import json,hashlib,sys
p=Path.home()/'workspaces/artifactory-lab/next/P2b-attribution'
c=json.loads(sys.stdin.read());split=c['split'];sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
assert sha(p/'SOURCES-SHA256.txt')==c['source_manifest_sha256']
files={}
for arm in ('shared','pergw','pad','aa'):
    folder=p/'data'/arm;s=json.loads((folder/f'{split}-sealed.json').read_text())
    assert s['complete'] and s['trials']==c['size'] and s['cleanup_absence_proved']
    assert s['source_manifest_sha256']==c['source_manifest_sha256']
    wanted={f'{split}-{suffix}' for suffix in ('provision.json','schedule.json','server.json','server-log-lines.txt','cleanup.json')}
    assert set(s['evidence_sha256'])==wanted
    hashes={**s['evidence_sha256'],f'{split}.jsonl':s['sha256']}
    for name,h in hashes.items():
        assert sha(folder/name)==h
        files[f'data/{arm}/{name}']=h
    files[f'data/{arm}/{split}-sealed.json']=sha(folder/f'{split}-sealed.json')
native=p/'data'/f'audit-{split}.json';a=json.loads(native.read_text())
assert a['passed'] is True and a['split']==split and len(a['runs'])==4
assert {r['arm'] for r in a['runs']}=={'shared','pergw','pad','aa'}
assert all(r['passed'] and r['trials']==c['size'] for r in a['runs'])
assert a['source_manifest_sha256']==c['source_manifest_sha256']
files[f'data/audit-{split}.json']=sha(native)
if split!='smoke':
    model=p/'DECODERS-FROZEN.json'
    if split=='holdout':assert sha(model)==c['prior_model_sha256']
    files['DECODERS-FROZEN.json']=sha(model)
print(json.dumps({'source_manifest_sha256':c['source_manifest_sha256'],'sha256':files}))
"""
    remote = subprocess.run(["ssh", "labhost", "python3", "-c", shlex.quote(script)],
                            input=json.dumps(config), text=True, capture_output=True, timeout=45)
    if remote.returncode:
        raise RuntimeError("Complete public split refused: " + remote.stderr)
    manifest = json.loads(remote.stdout)
    wanted = manifest["sha256"]
    expected = {
        f"data/{arm}/{split}{suffix}" for arm in p2b.ARMS for suffix in
        (".jsonl", "-provision.json", "-schedule.json", "-server.json",
         "-server-log-lines.txt", "-cleanup.json", "-sealed.json")
    } | {f"data/audit-{split}.json"}
    if split != "smoke":
        expected.add("DECODERS-FROZEN.json")
    if set(wanted) != expected or manifest["source_manifest_sha256"] \
            != config["source_manifest_sha256"]:
        raise RuntimeError("Unexpected public transport envelope")
    archive = HERE / f"{split}-edition4-public.tar.gz"
    command = "cd ~/workspaces/artifactory-lab/next/P2b-attribution && tar -czf - " \
        + " ".join(shlex.quote(name) for name in sorted(wanted))
    with archive.open("xb") as stream:
        downloaded = subprocess.run(["ssh", "labhost", command], stdout=stream,
                                    stderr=subprocess.PIPE, timeout=60)
    if downloaded.returncode:
        raise RuntimeError("Retained failed public transfer: " + downloaded.stderr.decode())
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) != len(wanted) or {m.name for m in members} != set(wanted):
            raise RuntimeError("Archive does not contain the exact public split")
        for member in members:
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError("Unexpected public archive member type")
            content = tar.extractfile(member).read()
            if hashlib.sha256(content).hexdigest() != wanted[member.name]:
                raise RuntimeError("Public payload changed during transfer")
            target = STUDY / member.name
            if target.exists():
                if target.read_bytes() != content:
                    raise RuntimeError("Preserve a different existing local payload")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(content)
    if any(sha(STUDY / name) != expected_sha for name, expected_sha in wanted.items()):
        raise RuntimeError("Local public inventory differs")
    runs = [audit.audit_arm(STUDY / "data" / arm, arm, split) for arm in p2b.ARMS]
    replay = {
        "split": split, "passed": all(row["passed"] for row in runs), "runs": runs,
        "audit_source_sha256": sha(STUDY / "audit.py"),
        "source_manifest_sha256": config["source_manifest_sha256"],
    }
    if not replay["passed"] or replay != json.loads(
            (STUDY / "data" / f"audit-{split}.json").read_text()):
        raise RuntimeError("Local native replay differs from the retained complete audit")
    if split != "smoke":
        analyze.verify_freeze(STUDY / "data")
    record = {
        "utc": datetime.now(timezone.utc).isoformat(), "split": split,
        "passed": True, "public_files": len(wanted), "sha256": wanted,
        "native_checks": sum(row["checks"] for row in runs),
        "trials": 4 * p2b.SIZES[split], "native_replay_exact": True,
        "archive_sha256": sha(archive), "transport_source_sha256": sha(Path(__file__)),
        "native_requests": 0, "provider_calls": 0,
        "private_runtime_exported": False,
    }
    with result_path.open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: record[key] for key in
                      ("passed", "split", "public_files", "native_checks", "trials")}))


if __name__ == "__main__":
    main()
