"""Operational evidence gate for the already frozen P4 subject study.

Does not dispatch models, mutate a protocol, or weaken a failed result.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
P2 = EVIDENCE / "P2-residual"
P4 = EVIDENCE / "P4-models"
P3 = EVIDENCE / "P3-linux"
VERIFICATION = EVIDENCE / "P2-verification"
sys.path.insert(0, str(P4))
import audit as subject_audit
import gate
import provider


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    require(read(P2 / "controller-status.json")["stage"] == "results_ready_for_lead",
            "P2 must finish before constructing the paid subject gate")
    gate.sources()
    verification_policy = read(VERIFICATION / "VERIFICATION-FROZEN.json")
    for name, expected in verification_policy["sha256"].items():
        require(sha(VERIFICATION / name) == expected, "Independent verification source changed")
    require(sha(P2 / "FROZEN.json") == verification_policy["p2_model_freeze_sha256"],
            "Independent verification refers to another model freeze")
    freeze = read(P2 / "FROZEN.json")
    data, output = P2 / freeze["data"], P2 / freeze["out"]
    verified = read(VERIFICATION / "RESULT.json")
    require(verified["passed"] is True and verified["primary_aa_null_passed"] is True
            and verified["holdout_results_sha256"] == sha(output / "holdout-results.json")
            and verified["verification_freeze_sha256"]
            == sha(VERIFICATION / "VERIFICATION-FROZEN.json"),
            "Current independent verification and A/A null must pass")
    # A fresh process keeps P2 and P4 modules with common names separate.
    subprocess.run([sys.executable, "-c",
                    "import p2b; p2b.check_sources(); p2b.p2_gate()"],
                   cwd=EVIDENCE / "P2b-attribution", check=True)

    utility_freeze = read(P3 / "UTILITY-FROZEN.json")
    for name, expected in utility_freeze["sha256"].items():
        require(sha(P3 / name) == expected, "Utility source changed")
    for name, expected in utility_freeze["import_sha256"].items():
        require(sha(EVIDENCE / name) == expected, "Utility import changed")
    utility_data = P3 / "data/u1"
    utility = read(utility_data / "audit-results.json")
    require(utility["passed"] is True and utility["identical_client_image"] is True
            and utility["audit_source_sha256"] == sha(P3 / "audit.py")
            and len(utility["arms"]) == 2
            and {row["mode"] for row in utility["arms"]} == {"separate", "immutable"}
            and all(row["passed"] is True and row["completed"] == 28
                    and row["scheduled"] == 28 and row["successful_tasks"] == 28
                    for row in utility["arms"]),
            "Complete matched Linux utility is required")
    utility_script = """
from pathlib import Path
import json, audit
p=Path.cwd(); f=json.loads((p/'UTILITY-FROZEN.json').read_text())
results=[audit.audit_arm(p/'data/u1'/mode,mode,f) for mode in f['arms']]
if not all(r['passed'] and r['completed']==28 and r['successful_tasks']==28 for r in results):
    raise RuntimeError('Read-only native utility recheck failed')
print('56 native utility tasks rechecked without rewriting the original audit')
"""
    subprocess.run([sys.executable, "-c", utility_script], cwd=P3, check=True)
    require(all(sha(utility_data / mode / "sealed.json") == expected
                for mode, expected in utility["sealed_sha256"].items()),
            "Utility seals changed")

    smoke_path = P4 / "gate-evidence/subject-smoke-audit.json"
    smoke = read(smoke_path)
    require(smoke["passed"] is True and smoke["scripted"] is True
            and smoke["model_calls"] == 0
            and smoke["source_freeze_sha256"] == sha(P4 / "SOURCES-FROZEN.json")
            and smoke["linux_utility_audit_sha256"] == sha(utility_data / "audit-results.json"),
            "Fresh scripted smoke must refer to current sources and utility")
    rerun = [subject_audit.audit_remote(
        P4 / "remote-data/smoke-s2" / f"p4-s2-{arm}", sha(P4 / "SOURCES-FROZEN.json"))
        for arm in ("open", "separate", "immutable")]
    require(rerun == smoke["runs"] and all(row["passed"] for row in rerun),
            "Read-only native smoke recheck failed")

    ledger = provider.budget.Ledger()
    summary = ledger.summary()
    reserve_path = EVIDENCE / "VM2/BUDGET-RESERVATION.json"
    reserve = read(reserve_path)
    require(reserve["canonical_guard_sha256"] == sha(EVIDENCE / "P-budget/GUARD-FROZEN.json")
            and reserve["price_source_sha256"] == sha(EVIDENCE / reserve["price_source"])
            and reserve["resource_readback_sha256"]
            == sha(EVIDENCE / "VM2/cost-resource-readback.json")
            and reserve["instance_metadata_sha256"] == sha(EVIDENCE / "VM2/instance.json")
            and Decimal(reserve["base_reserved_usd"]) == Decimal(summary["base_reserved_usd"])
            and Decimal(reserve["shared_subject_ceiling_usd"]) == Decimal(summary["stage_cap_usd"])
            and Decimal(reserve["lab2_reserved_usd"])
            == Decimal(str(reserve["reservation_hours"]))
            * Decimal(reserve["all_in_hourly_allowance_usd"])
            and Decimal(summary["subject_guarded_usd"]) <= Decimal(summary["stage_cap_usd"])
            and datetime.now(timezone.utc) < datetime.fromisoformat(reserve["reserve_end_utc"])
            and not summary["must_stop"],
            "Canonical subject guard and supplemental host reservation must remain valid")
    full_reserve = (Decimal(summary["base_reserved_usd"])
                    + Decimal(summary["stage_cap_usd"]) + Decimal(reserve["lab2_reserved_usd"]))
    require(full_reserve == Decimal(reserve["all_in_project_reserved_usd"])
            and full_reserve <= Decimal(reserve["authorized_ceiling_usd"])
            and full_reserve <= Decimal(ledger.config["conditional_total_authorization_usd"]),
            "All-in reservations exceed or contradict the authorized ceiling")
    known_path = EVIDENCE / "P1-meter/results/analysis-v2/main-m1/known-channel-validation.json"
    known = read(known_path)
    require(known["passed"] is True and known["null_gate_passed"] is True,
            "Actual P1 known-mechanism validation and nulls must pass")

    files = {
        "p1-known-channel-validation.json": known_path,
        "p2-holdout-audit-results.json": data / "holdout-audit-results.json",
        "p2-holdout-results.json": output / "holdout-results.json",
        "p2-model-freeze.json": P2 / "FROZEN.json",
        "subject-smoke-audit.json": smoke_path,
        "p2-independent-verification.json": VERIFICATION / "RESULT.json",
        "p2-independent-verification-freeze.json": VERIFICATION / "VERIFICATION-FROZEN.json",
        "linux-utility-audit.json": utility_data / "audit-results.json",
        "linux-utility-freeze.json": P3 / "UTILITY-FROZEN.json",
        "linux-utility-report.json": utility_data / "utility-report.json",
        "lab2-budget-reservation.json": reserve_path,
        "subject-budget-freeze.json": EVIDENCE / "P-budget/GUARD-FROZEN.json",
        **{f"p2-holdout-seal-{arm}.json": data / arm / "holdout-sealed.json"
           for arm in ("separate", "sham", "aa", "immutable")},
    }
    for name, source in files.items():
        destination = P4 / "gate-evidence" / name
        require(not destination.exists() or sha(destination) == sha(source),
                "Preserve contradictory prior gate evidence")
        if not destination.exists():
            shutil.copy2(source, destination)
    decision = {
        "p1_validated": True, "p2_holdout_audit_passed": True, "p2_aa_passed": True,
        "scripted_subject_smoke_passed": True, "budget_guard_verified": True,
        "source_freeze_sha256": sha(P4 / "SOURCES-FROZEN.json"),
        "evidence_sha256": {name: sha(source) for name, source in files.items()},
        "all_in_project_reserved_usd": str(full_reserve),
        "operational_helper_sha256": sha(Path(__file__)),
    }
    target = P4 / "ALLOW-SUBJECTS.json"
    if target.exists():
        require(read(target) == decision, "Preserve a different prior subject decision")
    else:
        with target.open("x") as stream:
            json.dump(decision, stream, indent=2)
    gate.check()
    print(json.dumps({"gate_passed": True, "subject_calls_dispatched": 0,
                      "all_in_project_reserved_usd": str(full_reserve),
                      "canonical_budget_summary": summary}, indent=2))


if __name__ == "__main__":
    main()
