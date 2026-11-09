"""Verify copied actual earlier-study evidence and the reserved owned host."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
NAMES = {
    "p2-independent-result.json", "p2b-results.json", "p2b-independent-result.json",
    "p2b-audit-holdout.json", "p2b-decoders-frozen.json", "p2b-held-review.json",
    "p2b-source-manifest.txt", "lab2-budget-reservation.json",
    "p9-cleanup-control.json",
}
NAMES.update("p2b-holdout-seal-" + arm + ".json"
             for arm in ("shared", "pergw", "pad", "aa"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def validate_evidence(files):
    require(set(files) == NAMES, "Earlier-evidence gate closure differs")
    p2, p2b = files["p2-independent-result.json"], files["p2b-independent-result.json"]
    primary, native = files["p2b-results.json"], files["p2b-audit-holdout.json"]
    review = files["p2b-held-review.json"]
    require(p2["passed"] and p2["primary_aa_null_passed"],
            "Actual P2 independent validation and primary idle control must pass")
    require(p2b["passed"] and p2b["primary_aa_raw_nominal_pass"]
            and p2b["primary_aa_holm_pass"]
            and primary["arms"]["aa"]["p_one_sided"] >= .05
            and primary["arms"]["aa"]["p_holm"] >= .05,
            "Actual complete P2b arithmetic and primary idle controls must pass")
    require(set(primary["arms"]) == {"shared", "pergw", "pad", "aa"}
            and all(row["trials"] == 300 for row in primary["arms"].values()),
            "All original P2b denominators must remain")
    require(native["passed"] and len(native["runs"]) == 4
            and {row["arm"] for row in native["runs"]} == {"shared", "pergw", "pad", "aa"}
            and all(row["passed"] and row["trials"] == 300 for row in native["runs"]),
            "Complete P2b native audits must pass")
    require(review["passed"] and review["all_calibration_seals_precede_model"]
            and review["all_models_precede_all_held_trials"] and review["trials"] == 1200
            and review["trials_excluded"] == 0,
            "Actual complete P2b chronology review must pass")
    for arm in ("shared", "pergw", "pad", "aa"):
        seal = files["p2b-holdout-seal-" + arm + ".json"]
        require(seal["complete"] and seal["trials"] == 300
                and seal["cleanup_absence_proved"], "P2b clean seals must be complete")
    budget = files["lab2-budget-reservation.json"]
    require(budget["instance_id"] == "i-0de32c76945e63b44"
            and budget["region"] == "ap-south-1"
            and Decimal(budget["all_in_project_reserved_usd"]) == Decimal("38.1959800672")
            and Decimal(budget["authorized_ceiling_usd"]) == Decimal("50")
            and Decimal(budget["lab2_reserved_usd"]) == Decimal("3.20"),
            "The original full reservation and owned host must remain")
    cleanup = files["p9-cleanup-control.json"]
    require(cleanup["instance_id"] == budget["instance_id"]
            and cleanup["cutoff_utc"] == "2026-11-09T10:30:00+00:00"
            and cleanup["p9_public_inventory_supported"]
            and cleanup["incomplete_public_diagnostics_supported"],
            "Prospective owned inventory and cleanup expansion must precede data")


def check_gate():
    import p2b
    p2b.check_sources()
    gate = json.loads((HERE / "ALLOW-COLLECTION.json").read_text())
    require(gate["source_manifest_sha256"] == sha(HERE / "SOURCES-SHA256.txt")
            and set(gate["evidence_sha256"]) == NAMES,
            "Actual collection decision differs from scientific/evidence closure")
    for name, expected in gate["evidence_sha256"].items():
        require(sha(HERE / "gate-evidence" / name) == expected,
                "Copied actual gate evidence changed")
    files = {}
    for name in NAMES:
        value = (HERE / "gate-evidence" / name).read_bytes()
        if name == "p2b-source-manifest.txt":
            files[name] = value.decode()
        else:
            files[name] = json.loads(value)
    validate_evidence(files)
    require(files["p2b-independent-result.json"]["primary_results_sha256"]
            == gate["evidence_sha256"]["p2b-results.json"]
            and files["p2b-independent-result.json"]["native_audit_sha256"]
            == gate["evidence_sha256"]["p2b-audit-holdout.json"]
            and files["p2b-independent-result.json"]["decoder_freeze_sha256"]
            == gate["evidence_sha256"]["p2b-decoders-frozen.json"]
            and files["p2b-independent-result.json"]["verification_freeze_sha256"]
            == gate["prior_p2b_verification_freeze_sha256"],
            "Actual earlier-study numerical bindings differ")
    require(files["p2b-audit-holdout.json"]["source_manifest_sha256"]
            == gate["evidence_sha256"]["p2b-source-manifest.txt"],
            "Actual native audit/source binding differs")
    names = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"],
                                    text=True).splitlines()
    require(set(names) == {"art-a", "art-a-db"}, "Keep one owned workload on quiet lab2")
    require(p2b.old.free_gib() >= 2, "At least two GiB of available memory are required")
