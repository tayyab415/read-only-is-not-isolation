"""Prospective P9 arithmetic with integer conditional assignment weights."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent / "P9-content"
CELLS = ("equal", "distinct", "aa-equal", "aa-distinct")
REFERENCE = HERE.parent / "P2b-verification/verify.py"
sys.path.insert(0, str(STUDY))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(value, message):
    if not value:
        raise RuntimeError(message)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def main():
    policy = read(HERE / "VERIFICATION-FROZEN.json")
    require(all(sha(HERE / name) == expected for name, expected in policy["sha256"].items())
            and sha(REFERENCE) == policy["integer_reference_sha256"]
            and sha(STUDY / "SOURCES-SHA256.txt") == policy["p9_source_manifest_sha256"],
            "Prospective verifier, arithmetic reference or scientific edition changed")
    arithmetic = module("p9_integer_reference", REFERENCE)
    collector = module("p2b", STUDY / "p2b.py")
    analyze = module("analyze", STUDY / "analyze.py")
    audit = module("p9_native_audit", STUDY / "audit.py")
    collector.check_sources()
    data = STUDY / "data"
    frozen = analyze.verify_freeze(data)
    analyze.check_split_audit(data, "holdout")
    native = read(data / "audit-holdout.json")
    replayed = [audit.audit_arm(data / cell, cell, "holdout") for cell in CELLS]
    require(replayed == native["runs"] and all(row["passed"] for row in replayed),
            "Complete read-only native audit differs")
    primary = read(STUDY / "results.json")
    require(primary["freeze_sha256"] == sha(STUDY / "DECODERS-FROZEN.json")
            and primary["audit_sha256"] == sha(data / "audit-holdout.json")
            and set(primary["arms"]) == set(CELLS), "Primary closure differs")
    scores, probabilities, chronology = {}, [], {}
    frozen_ns = int(datetime.fromisoformat(frozen["frozen_utc"]).timestamp() * 1e9)
    for cell in CELLS:
        calibration = [json.loads(line) for line in
                       (data / cell / "calibration.jsonl").read_text().splitlines()]
        require(len(calibration) == 100 and
                Counter((row["label"], row["sender"]) for row in calibration) ==
                Counter({(label, sender): 25 for label in (0, 1)
                         for sender in ("alice", "bob")}), "Calibration balance differs")
        medians = {}
        for label in (0, 1):
            values = []
            for row in calibration:
                if row["label"] == label:
                    timed = [r["elapsed_ms"] for r in row["post"]
                             if str(r.get("probe", "")).startswith("post.timed.")]
                    require(len(timed) == 15 and all(math.isfinite(v) and v >= 0 for v in timed),
                            "Invalid calibration probes")
                    values.append(sorted(timed)[7])
            medians[label] = statistics.median(values)
        decoder = frozen["decoders"][cell]
        require(decoder["threshold_ms"] == (medians[0] + medians[1]) / 2
                and decoder["active_is_below"] == (medians[1] < medians[0]),
                "The original calibration fitting rule differs")
        cal_seal = read(data / cell / "calibration-sealed.json")
        held = [json.loads(line) for line in (data / cell / "holdout.jsonl").read_text().splitlines()]
        first_ns = min(row["controller_start_utc_ns"] for row in held)
        require(cal_seal["sealed_utc_ns"] < frozen_ns < first_ns,
                "Calibration/model/fresh holdout chronology differs")
        chronology[cell] = {"calibration_seal_utc_ns": cal_seal["sealed_utc_ns"],
                            "model_utc_ns": frozen_ns, "first_held_utc_ns": first_ns}
        score = arithmetic.score(held, decoder)
        probabilities.append(score.pop("conditional_sensitivity_p"))
        score.pop("primary_binomial_p")
        scores[cell] = score
    adjusted = arithmetic.holm(probabilities)
    for cell, p, q in zip(CELLS, probabilities, adjusted):
        computed, original = scores[cell], primary["arms"][cell]
        for field in ("trials", "correct", "failed_trials"):
            require(computed[field] == original[field], "Primary count differs")
        for field in ("accuracy", "median_S_active_ms", "median_S_idle_ms",
                      "probe_latency_median_ms", "probe_latency_p95_ms"):
            require(arithmetic.close(computed[field], original[field]), "Primary number differs")
        require(arithmetic.close_probability(p, original["p_one_sided"])
                and arithmetic.close_probability(q, original["p_holm"]),
                "Integer conditional probability or Holm correction differs")
        computed.update(primary_conditional_p=float(p), primary_holm_p=float(q))
    comparison = {
        "fisher_p_two_sided": float(arithmetic.fisher_two_sided(
            scores["equal"]["correct"], scores["distinct"]["correct"], 300)),
        "accuracy_difference": scores["equal"]["accuracy"] - scores["distinct"]["accuracy"]}
    require(all(arithmetic.close_probability(value, primary["comparisons"]["equal_vs_distinct"][key])
                if key.startswith("fisher") else
                arithmetic.close(value, primary["comparisons"]["equal_vs_distinct"][key])
                for key, value in comparison.items()), "Secondary comparison differs")
    nulls = {cell: {"raw_pass": scores[cell]["primary_conditional_p"] >= .05,
                    "holm_pass": scores[cell]["primary_holm_p"] >= .05}
             for cell in ("aa-equal", "aa-distinct")}
    null_gate = all(all(value.values()) for value in nulls.values())
    require(primary["null_gate_passed"] == null_gate, "Primary null decision differs")
    result = {
        "utc": datetime.now(timezone.utc).isoformat(), "passed": True,
        "scores": scores, "comparison": comparison, "nulls": nulls,
        "null_gate_passed": null_gate, "chronology": chronology,
        "verification_freeze_sha256": sha(HERE / "VERIFICATION-FROZEN.json"),
        "primary_results_sha256": sha(STUDY / "results.json"),
        "model_freeze_sha256": sha(STUDY / "DECODERS-FROZEN.json"),
        "native_audit_sha256": sha(data / "audit-holdout.json"),
        "scope": "Separate exact arithmetic; original native auditor is reused. Arithmetic agreement does not guarantee passing idle controls."}
    with (HERE / "RESULT.json").open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"passed": True, "null_gate_passed": null_gate}))


if __name__ == "__main__":
    main()
