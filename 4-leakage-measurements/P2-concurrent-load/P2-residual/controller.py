"""Advance the authorized P2 sequence. Holdout collection follows all fit gates."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
DATA = HERE / "data/p2-m1"
OUT = HERE / "results/p2-m1"
CONFIGS = ["separate", "sham", "aa", "immutable"]
REMOTE = "workspaces/artifactory-lab/next/P2-residual"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
       "-o", "ControlMaster=auto", "-o",
       "ControlPath=/tmp/p1-research-cm-%r@%h:%p",
       "-o", "ControlPersist=10m", "labhost"]
ENV = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
       "PYTHONDONTWRITEBYTECODE": "1"}
DEADLINE = datetime(2026, 11, 9, 10, 30, tzinfo=timezone.utc)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, timeout=kwargs.pop("timeout", 300),
                          text=True, capture_output=True, **kwargs).stdout


def remote(script):
    return run(SSH + ["python3", "-"], input=script, timeout=60)


def policy():
    frozen = json.loads((HERE / "CONTROL-FROZEN.json").read_text())
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Control/source drift: {name}")
    scientific_policy = json.loads((HERE / "SOURCES-FROZEN.json").read_text())
    for name, expected in scientific_policy["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Frozen scientific source drift: {name}")
    if sha(HERE.parent / "P1-meter/analysis.py") != scientific_policy["p1_analysis_sha256"]:
        raise RuntimeError("Imported analysis source drift")


def snapshot():
    return json.loads(remote("""
import json,hashlib
from pathlib import Path
p=Path.home()/'workspaces/artifactory-lab/next/P2-residual'
out={'source_sha256':hashlib.sha256((p/'SOURCES-FROZEN.json').read_bytes()).hexdigest()}
out['sequence_sha256']={n:hashlib.sha256((p/n).read_bytes()).hexdigest()
 for n in ['calibration-sequence.py','holdout-sequence.py'] if (p/n).exists()}
for phase in ['calibration','holdout']:
 s=p/(phase+'-sequence-status.json')
 out[phase]=json.loads(s.read_text()) if s.exists() else None
 out[phase+'_seals']={}
 for cfg in ['separate','sham','aa','immutable']:
  f=p/'data/p2-m1'/cfg/(phase+'-sealed.json')
  if f.exists():out[phase+'_seals'][cfg]=json.loads(f.read_text())
out['holdout_files_exist']=any((p/'data/p2-m1'/cfg/'holdout.jsonl').exists()
 for cfg in ['separate','sham','aa','immutable'])
print(json.dumps(out))
"""))


def ready(snapshot_value, phase):
    state = snapshot_value[phase]
    if state and state["stage"] == "failed":
        raise RuntimeError(f"Remote {phase} failed; retain and inspect the evidence")
    count = 300 if phase == "calibration" else 1000
    seals = snapshot_value[phase + "_seals"]
    return (state is not None and state["stage"] == phase + "_audited"
            and set(seals) == set(CONFIGS)
            and all(s.get("complete") and s.get("trials") == count
                    and s.get("cleanup_absence_proved") for s in seals.values()))


def fetch_phase(snapshot_value, phase):
    count = 300 if phase == "calibration" else 1000
    for cfg in CONFIGS:
        seal = snapshot_value[phase + "_seals"][cfg]
        expected = {
            phase + ".jsonl": seal["sha256"],
            phase + "-provision.json": seal["provision_sha256"],
            **seal["evidence_sha256"],
        }
        names = {phase + suffix for suffix in
                 [".jsonl", "-provision.json", "-schedule.json", "-server.json",
                  "-server-log-lines.txt", "-cleanup.json"]}
        if set(expected) != names or seal["trials"] != count:
            raise RuntimeError("Unexpected public evidence contract")
        for name in sorted(names | {phase + "-sealed.json"}):
            target = DATA / cfg / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and name in expected and sha(target) == expected[name]:
                continue
            temp = target.with_suffix(target.suffix + ".retrieving")
            run(["rsync", "-a", f"labhost:{REMOTE}/data/p2-m1/{cfg}/{name}", str(temp)])
            if name in expected and sha(temp) != expected[name]:
                raise RuntimeError(f"Transport hash mismatch: {cfg}/{name}")
            if name == phase + "-sealed.json" and json.loads(temp.read_text()) != seal:
                raise RuntimeError("Transported seal differs from readiness snapshot")
            temp.replace(target)


def scientific(argv):
    with (HERE / "controller.log").open("a") as stream:
        result = subprocess.run([sys.executable, *argv], env=ENV, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=1800)
    if result.returncode:
        raise RuntimeError("Scientific gate failed; inspect retained controller.log")


def verify_frozen_fit():
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    if frozen["data"] != "data/p2-m1" or frozen["out"] != "results/p2-m1" \
            or frozen.get("holdout_collection_allowed") is not True:
        raise RuntimeError("Frozen fit points to another dataset")
    for name, expected in frozen["sha256"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts or sha(HERE / name) != expected:
            raise RuntimeError("Frozen input changed or escaped its directory")
    return frozen


def start_holdout():
    frozen = verify_frozen_fit()
    upload = {"FROZEN.json", "data/p2-m1/calibration-audit-results.json",
              "results/p2-m1/calibration-results.json",
              *(f"results/p2-m1/{cfg}.model.pkl" for cfg in CONFIGS)}
    remote("""
from pathlib import Path
p=Path.home()/'workspaces/artifactory-lab/next/P2-residual'
assert not any((p/'data/p2-m1'/c/'holdout.jsonl').exists()
 for c in ['separate','sham','aa','immutable'])
(p/'results/p2-m1').mkdir(parents=True,exist_ok=True)
""")
    for name in sorted(upload):
        destination = str(Path(REMOTE) / Path(name).parent)
        run(["rsync", "-a", str(HERE / name), f"labhost:{destination}/"])
    remote("""
import json,hashlib,sys
from pathlib import Path
p=Path.home()/'workspaces/artifactory-lab/next/P2-residual'
f=json.loads((p/'FROZEN.json').read_text())
assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==v for n,v in f['sha256'].items())
sys.path.insert(0,str(p));import collect,service
service.check_gate();collect.verify_model_freeze(p/'data/p2-m1')
""")
    run(SSH + ["tmux new-session -d -s p2-holdout " + shlex.quote(
        "PYTHONDONTWRITEBYTECODE=1 python3 ~/" + REMOTE + "/holdout-sequence.py")],
        timeout=30)


def advance(value):
    policy()
    if value["source_sha256"] != sha(HERE / "SOURCES-FROZEN.json"):
        raise RuntimeError("Remote source freeze differs")
    for name in ["calibration-sequence.py", "holdout-sequence.py"]:
        if value.get("sequence_sha256", {}).get(name) != sha(HERE / name):
            raise RuntimeError("Remote sequence source differs")
    if not (HERE / "FROZEN.json").exists():
        if value["holdout_files_exist"]:
            raise RuntimeError("Holdout exists before local freeze")
        if not ready(value, "calibration"):
            return "collecting_calibration"
        fetch_phase(value, "calibration")
        scientific([str(HERE / "audit.py"), str(DATA), "calibration"])
        if not (OUT / "calibration-results.json").exists():
            scientific([str(HERE / "analysis.py"), "calibrate", str(DATA), str(OUT)])
        scientific([str(HERE / "analysis.py"), "freeze", str(DATA), str(OUT)])
    verify_frozen_fit()
    if value["holdout"] is None:
        start_holdout()
        return "holdout_started"
    if not ready(value, "holdout"):
        return "collecting_holdout"
    fetch_phase(value, "holdout")
    scientific([str(HERE / "audit.py"), str(DATA), "holdout"])
    if not (OUT / "holdout-results.json").exists():
        scientific([str(HERE / "analysis.py"), "holdout", str(DATA), str(OUT)])
    return "results_ready_for_lead"


def main():
    with (HERE / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        failures = 0
        while True:
            if datetime.now(timezone.utc) >= DEADLINE:
                raise RuntimeError("Continuation cutoff reached; retain unfinished work")
            try:
                value = snapshot()
                stage = advance(value)
                record = {"utc": datetime.now(timezone.utc).isoformat(),
                          "pid": os.getpid(), "stage": stage, "remote": value}
                (HERE / "controller-status.json").write_text(json.dumps(record, indent=2))
                print(json.dumps({"utc": record["utc"], "stage": stage}), flush=True)
                failures = 0
                if stage == "results_ready_for_lead":
                    return
            except Exception as error:
                failures += 1
                (HERE / "controller-error.json").write_text(json.dumps({
                    "utc": datetime.now(timezone.utc).isoformat(),
                    "type": type(error).__name__, "message": str(error),
                    "consecutive_failures": failures}))
                if failures >= 3 or isinstance(error, RuntimeError):
                    raise
            time.sleep(120)


if __name__ == "__main__":
    main()
