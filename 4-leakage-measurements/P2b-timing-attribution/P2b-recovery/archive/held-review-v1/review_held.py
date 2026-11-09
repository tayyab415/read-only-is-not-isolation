"""Review complete P2b chronology and timing without changing primary results."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent / "P2b-attribution"
VERIFY = HERE.parent / "P2b-verification"
ARMS = ("shared", "pergw", "pad", "aa")
DATA = STUDY / "data"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def distribution(values):
    ordered = sorted(values)
    return {
        "count": len(ordered), "min": ordered[0], "median": statistics.median(ordered),
        "p95_nearest_rank": ordered[math.ceil(.95 * len(ordered)) - 1],
        "max": ordered[-1],
    }


def main():
    destination = HERE / "HELD-REVIEW.json"
    require(not destination.exists(), "Preserve the existing complete held review")
    # Require closed transfer and numerical verification before any held row.
    require((HERE / "TRANSPORT-holdout.json").exists() and
            (VERIFY / "RESULT.json").exists(),
            "Complete held transport and original/independent analysis are required")
    transport = read(HERE / "TRANSPORT-holdout.json")
    verification = read(VERIFY / "RESULT.json")
    calibration = read(HERE / "CALIBRATION-REVIEW.json")
    model = read(STUDY / "DECODERS-FROZEN.json")
    native = read(DATA / "audit-holdout.json")
    require(transport["passed"] and verification["passed"] and calibration["passed"],
            "Actual transfer, numerical verification and calibration review must pass")
    require(verification["decoder_freeze_sha256"] ==
            calibration["model_sha256"] == sha(STUDY / "DECODERS-FROZEN.json"),
            "The model must match the prior calibration review")
    require(verification["native_audit_sha256"] == sha(DATA / "audit-holdout.json")
            and verification["primary_results_sha256"] == sha(STUDY / "results.json"),
            "Original analysis/native bindings changed")
    require(native["passed"] and len(native["runs"]) == 4
            and {row["arm"] for row in native["runs"]} == set(ARMS)
            and all(row["passed"] and row["trials"] == 300 for row in native["runs"]),
            "All four full held native audits must pass")
    frozen_ns = round(datetime.fromisoformat(model["frozen_utc"]).timestamp() * 1e9)
    inputs = {
        "P2b-recovery/CALIBRATION-REVIEW.json": sha(HERE / "CALIBRATION-REVIEW.json"),
        "P2b-recovery/TRANSPORT-holdout.json": sha(HERE / "TRANSPORT-holdout.json"),
        "P2b-verification/RESULT.json": sha(VERIFY / "RESULT.json"),
        "P2b-attribution/DECODERS-FROZEN.json": sha(STUDY / "DECODERS-FROZEN.json"),
        "P2b-attribution/results.json": sha(STUDY / "results.json"),
        "P2b-attribution/data/audit-holdout.json": sha(DATA / "audit-holdout.json"),
    }
    # Check every complete seal and hash before parsing any observations.
    seals = {}
    for arm in ARMS:
        folder = DATA / arm
        seal = read(folder / "holdout-sealed.json")
        require(seal["complete"] and seal["trials"] == 300
                and seal["cleanup_absence_proved"],
                "Incomplete held cell or cleanup")
        require(calibration["arms"][arm]["sealed_utc_ns"] < frozen_ns,
                "Calibration did not close before the common freeze")
        hashes = {**seal["evidence_sha256"], "holdout.jsonl": seal["sha256"],
                  "holdout-sealed.json": sha(folder / "holdout-sealed.json")}
        for name, expected in hashes.items():
            require(sha(folder / name) == expected, "A sealed held input changed")
            inputs[f"P2b-attribution/data/{arm}/{name}"] = expected
        seals[arm] = seal
    summaries = {}
    for arm in ARMS:
        folder = DATA / arm
        trials = [json.loads(line) for line in (folder / "holdout.jsonl").read_text().splitlines()]
        first_start = min(row["controller_start_utc_ns"] for row in trials)
        require(frozen_ns < first_start and len(trials) == 300,
                "Fresh held chronology or full denominator differs")
        leads, overruns, durations, late, positive = [], [], [], [], []
        for number, row in enumerate(trials, 1):
            epoch = row["epoch_utc_ns"]
            lead = min((epoch - r["schedule_setup_utc_ns"]) / 1e6 for r in row["post"])
            overrun = max(0, max((r["end_utc_ns"] for r in row["sender_records"]),
                                default=epoch) - epoch - 2_000_000_000) / 1e6
            require(math.isclose(lead, row["receiver_schedule_lead_ms"], abs_tol=1e-8)
                    and math.isclose(overrun, row["sender_overrun_ms"], abs_tol=1e-8),
                    "Recorded scheduling diagnostic does not recompute")
            leads.append(lead)
            overruns.append(overrun)
            durations.append((row["controller_end_utc_ns"] -
                              row["controller_start_utc_ns"]) / 1e9)
            witness = {
                "trial": row["trial_id"], "label": row["label"],
                "sender": row["sender"], "receiver": row["receiver"],
                "jsonl_line": number, "receiver_schedule_lead_ms": lead,
                "sender_overrun_ms": overrun,
            }
            if lead < 0:
                late.append(witness)
            if overrun > 0:
                positive.append(witness)
        summaries[arm] = {
            "trials": 300, "first_controller_start_utc_ns": first_start,
            "last_controller_end_utc_ns": max(t["controller_end_utc_ns"] for t in trials),
            "sealed_utc_ns": seals[arm]["sealed_utc_ns"],
            "model_precedes_first_held_trial": True,
            "receiver_schedule_lead_ms": distribution(leads),
            "late_receiver_setup_trials": late,
            "sender_overrun_ms_all_trials": distribution(overruns),
            "positive_sender_overrun_trials": positive,
            "controller_trial_wall_seconds": distribution(durations),
            "failed_trials": sum(bool(t["failed_request_markers"]) for t in trials),
            "sender_http_requests": sum(len(t["sender_records"]) for t in trials),
            "receiver_http_requests": sum(len(t["post"]) for t in trials),
        }
        if arm == "pad":
            server = read(folder / "holdout-server.json")
            wanted = {r["marker"] for t in trials for r in t["post"] + t["sender_records"]}
            selected = [g for g in server["gateway_log"] if g["marker"] in wanted]
            require(len(selected) == len(wanted)
                    and {g["marker"] for g in selected} == wanted,
                    "Primary padded requests do not join every native gateway row")
            delays = [(g["headers_started_utc_ns"] - g["start_utc_ns"]) / 1e6
                      for g in selected]
            require(all(g["min_response_ms"] == 12 for g in selected)
                    and min(delays) >= 12, "The frozen 12 ms response floor failed")
            summaries[arm]["native_headers_begin_delay_ms"] = distribution(delays)
            summaries[arm]["primary_gateway_floor_checked_requests"] = len(selected)
    require(all(sha(HERE.parent / name) == expected
                for name, expected in inputs.items()), "Reviewed input inventory changed")
    record = {
        "utc": datetime.now(timezone.utc).isoformat(), "passed": True,
        "scope": "Complete held chronology and descriptive timing; original primary family unchanged",
        "model_frozen_utc": model["frozen_utc"], "model_sha256": calibration["model_sha256"],
        "all_calibration_seals_precede_model": True,
        "all_models_precede_all_held_trials": True, "trials": 1200,
        "native_checks": sum(r["checks"] for r in native["runs"]),
        "arms": summaries, "input_sha256": inputs,
        "primary_aa_raw_nominal_pass": verification["primary_aa_raw_nominal_pass"],
        "primary_aa_holm_pass": verification["primary_aa_holm_pass"],
        "conditional_aa_sensitivity_pass": verification["conditional_aa_sensitivity_pass"],
        "review_source_sha256": sha(Path(__file__)),
        "original_outputs_overwritten": False, "trials_excluded": 0,
        "native_or_provider_requests": 0,
        "limits": [
            "Scheduling and overruns are descriptive and do not select or exclude trials.",
            "The 12 ms floor constrains header delivery, not all latency variation.",
            "Per-tenant gateways double total gateway CPU and do not isolate one internal cause.",
            "Arithmetic agreement does not repair a failed primary or idle-control result.",
        ],
    }
    destination.open("x").write(json.dumps(record, indent=2) + "\n")
    print(json.dumps({
        "passed": True, "trials": 1200, "native_checks": record["native_checks"],
        "all_models_precede_all_held_trials": True,
        "late_receiver_setups": {a: len(s["late_receiver_setup_trials"])
                                 for a, s in summaries.items()},
        "positive_sender_overruns": {a: len(s["positive_sender_overrun_trials"])
                                    for a, s in summaries.items()},
    }))


if __name__ == "__main__":
    main()
