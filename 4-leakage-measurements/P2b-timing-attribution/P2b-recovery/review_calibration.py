"""Check the complete retained calibration and model without opening holdouts."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent / "P2b-attribution"
sys.path.insert(0, str(STUDY))
import p2b

spec = importlib.util.spec_from_file_location("review_p2b_analyze", STUDY / "analyze.py")
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    destination = HERE / "CALIBRATION-REVIEW.json"
    if destination.exists():
        raise RuntimeError("Preserve the existing complete calibration review")
    p2b.check_sources()
    transport = json.loads((HERE / "TRANSPORT-calibration.json").read_text())
    if not transport["passed"] or transport["trials"] != 400 \
            or transport["public_files"] != 30 or not transport["native_replay_exact"]:
        raise RuntimeError("Complete public calibration/native replay is required")
    frozen = analyze.verify_freeze(STUDY / "data")
    freeze_ns = int(datetime.fromisoformat(frozen["frozen_utc"]).timestamp() * 1e9)
    arms = {}
    for arm in p2b.ARMS:
        folder = STUDY / "data" / arm
        seal = json.loads((folder / "calibration-sealed.json").read_text())
        if not seal["complete"] or seal["trials"] != 100 \
                or not seal["cleanup_absence_proved"] or seal["sealed_utc_ns"] >= freeze_ns:
            raise RuntimeError("Every complete clean calibration must precede the model")
        trials = analyze.load(folder / "calibration.jsonl")
        classes = {label: [analyze.statistic(row) for row in trials if row["label"] == label]
                   for label in (0, 1)}
        if any(len(values) != 50 for values in classes.values()):
            raise RuntimeError("Retain the original balanced 100-trial denominator")
        medians = {label: statistics.median(values) for label, values in classes.items()}
        decoder = frozen["decoders"][arm]
        if decoder["threshold_ms"] != (medians[0] + medians[1]) / 2 \
                or decoder["active_is_below"] != (medians[1] < medians[0]) \
                or decoder["calibration_median_active_ms"] != medians[1] \
                or decoder["calibration_median_idle_ms"] != medians[0]:
            raise RuntimeError("The retained decoder differs from its frozen fitting rule")
        arms[arm] = {"trials": 100, "clean": True,
                     "sealed_utc_ns": seal["sealed_utc_ns"],
                     "calibration_seal_sha256": sha(folder / "calibration-sealed.json"),
                     "decoder": decoder}
    record = {
        "utc": datetime.now(timezone.utc).isoformat(), "passed": True,
        "model_frozen_utc": frozen["frozen_utc"],
        "model_sha256": sha(STUDY / "DECODERS-FROZEN.json"),
        "source_manifest_sha256": sha(STUDY / "SOURCES-SHA256.txt"),
        "transport_sha256": sha(HERE / "TRANSPORT-calibration.json"),
        "all_calibration_seals_precede_model": True,
        "native_checks": transport["native_checks"], "arms": arms,
        "held_observations_opened": False, "decoder_or_science_changed": False,
        "native_or_provider_requests": 0, "review_source_sha256": sha(Path(__file__)),
        "scope": "Complete calibration/model review; held chronology and nulls remain pending",
    }
    with destination.open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: record[key] for key in
                      ("passed", "model_frozen_utc", "model_sha256", "native_checks")}))


if __name__ == "__main__":
    main()
