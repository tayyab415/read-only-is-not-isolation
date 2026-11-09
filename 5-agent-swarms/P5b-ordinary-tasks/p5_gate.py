"""Own source and evidence gates, plus the unchanged P4/P-budget gates."""
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
P4 = HERE.parent / "P4-models"
sys.path.insert(0, str(P4))
import gate as p4_gate
import schedule as p4_schedule
sys.path.insert(0, str(HERE.parent / "P4-audit-recovery"))
import binding as p4_validation


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def sources():
    frozen = read(HERE / "SOURCES-FROZEN.json")
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError("Ordinary-task source changed")
    for name, expected in frozen["import_sha256"].items():
        if sha(HERE.parent / name) != expected:
            raise RuntimeError("Ordinary-task imported source changed")
    p4_gate.sources()
    return frozen


def assigned_complete(assigned):
    expected = p4_schedule.assigned()
    return (assigned["scheduled_pairs"] == len(expected)
            and len(assigned["rows"]) == len(expected)
            and assigned["source_freeze_sha256"] == sha(P4 / "SOURCES-FROZEN.json")
            and {(row["run_id"], row["model"], row["configuration"]) for row in assigned["rows"]}
            == {(row["run_id"], row["model"], row["configuration"]) for row in expected}
            and all(row["collected"] and row.get("audit_passed") for row in assigned["rows"]))


def check():
    sources()
    p4_gate.check()
    if (HERE / "STOP.json").exists():
        raise RuntimeError("Ordinary-task stop blocks new calls")
    allowed = read(HERE / "ALLOW-ORDINARY.json")
    if not allowed["p4_subject_collection_closed"] or not allowed["scripted_ordinary_smoke_passed"] \
            or allowed["source_freeze_sha256"] != sha(HERE / "SOURCES-FROZEN.json"):
        raise RuntimeError("Ordinary-task collection has not been allowed")
    required = {"p4-assigned-results.json", "ordinary-smoke-audit.json",
                "p4-validator-amendment.json", "p4-amended-replay.json"}
    if not required.issubset(allowed["evidence_sha256"]):
        raise RuntimeError("Ordinary-task gate evidence is incomplete")
    for name, expected in allowed["evidence_sha256"].items():
        file = (HERE / "gate-evidence" / name).resolve()
        if (HERE / "gate-evidence").resolve() not in file.parents or sha(file) != expected:
            raise RuntimeError("Ordinary-task gate evidence changed")
    assigned = read(HERE / "gate-evidence/p4-assigned-results.json")
    smoke = read(HERE / "gate-evidence/ordinary-smoke-audit.json")
    amendment = read(HERE / "gate-evidence/p4-validator-amendment.json")
    replay = read(HERE / "gate-evidence/p4-amended-replay.json")
    p4_validation.validate()
    if sha(HERE / "gate-evidence/p4-validator-amendment.json") \
            != sha(p4_validation.HERE / "AMENDMENT.json") \
            or replay.get("passed") is not True \
            or replay["amendment_sha256"] != sha(HERE / "gate-evidence/p4-validator-amendment.json") \
            or replay["report_sha256"] != sha(HERE / "gate-evidence/p4-assigned-results.json") \
            or replay["original_source_freeze_sha256"] != sha(P4 / "SOURCES-FROZEN.json") \
            or amendment["client_tolerance_ns_unchanged"] != 2_000_000:
        raise RuntimeError("The assigned result lacks its declared native validator binding")
    if not assigned_complete(assigned) or not smoke["passed"] or not smoke["scripted"]:
        raise RuntimeError("Gate decisions contradict the subject/smoke evidence")
    return allowed


def stop(reason):
    try:
        with (HERE / "STOP.json").open("x") as stream:
            json.dump({"reason": reason, "resume_requires_recorded_investigation": True}, stream, indent=2)
    except FileExistsError:
        pass
