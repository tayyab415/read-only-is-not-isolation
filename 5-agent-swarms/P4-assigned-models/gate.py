"""File-backed scientific gates for calls and owned deployment."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def sources():
    frozen = read(HERE / "SOURCES-FROZEN.json")
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError(f"Frozen subject-stage source changed: {name}")
    for name, expected in frozen["import_sha256"].items():
        if sha(HERE.parent / name) != expected:
            raise RuntimeError(f"Frozen subject-stage import changed: {name}")
    return frozen


def check():
    sources()
    if (HERE / "STOP.json").exists():
        raise RuntimeError("Retained infrastructure/cost stop blocks more subjects")
    decision = read(HERE / "ALLOW-SUBJECTS.json")
    if not all(decision.get(name) is True for name in (
            "p1_validated", "p2_holdout_audit_passed", "p2_aa_passed",
            "scripted_subject_smoke_passed", "budget_guard_verified")) \
            or decision["source_freeze_sha256"] != sha(HERE / "SOURCES-FROZEN.json"):
        raise RuntimeError("The subject-stage gates have not passed")
    expected = {"p1-known-channel-validation.json", "p2-holdout-audit-results.json",
                "p2-holdout-results.json", "p2-model-freeze.json", "subject-smoke-audit.json"}
    if not expected.issubset(decision["evidence_sha256"]):
        raise RuntimeError("Missing public scientific-gate inputs")
    for name, expected_sha in decision["evidence_sha256"].items():
        file = (HERE / "gate-evidence" / name).resolve()
        if (HERE / "gate-evidence").resolve() not in file.parents or sha(file) != expected_sha:
            raise RuntimeError("Scientific-gate evidence changed")
    known = read(HERE / "gate-evidence/p1-known-channel-validation.json")
    audit = read(HERE / "gate-evidence/p2-holdout-audit-results.json")
    results = read(HERE / "gate-evidence/p2-holdout-results.json")
    smoke = read(HERE / "gate-evidence/subject-smoke-audit.json")
    if not known["passed"] or not known["null_gate_passed"] \
            or not audit["passed"] or audit["smoke"] \
            or {row["configuration"] for row in audit["runs"]} \
            != {"separate", "sham", "aa", "immutable"} \
            or any(not row["passed"] or row["trials"] != 1000 for row in audit["runs"]) \
            or not results["aa_nominal_pass"] or not smoke["passed"] or not smoke["scripted"]:
        raise RuntimeError("The recorded gate decisions contradict their evidence")
    return decision


def stop(reason):
    try:
        with (HERE / "STOP.json").open("x") as stream:
            json.dump({"reason": reason, "resume_requires_recorded_investigation": True}, stream, indent=2)
    except FileExistsError:
        pass
