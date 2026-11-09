"""Describe the audited P7 evidence and plot the unchanged frozen decisions."""
from collections import Counter
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parents[2] / "evidence"
P7 = EVIDENCE / "P7-caches"
DATA = P7 / "data/g2-main"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    result = read(DATA / "holdout-results.json")
    audit = read(DATA / "audit-results.json")
    if not audit["passed"] or result["audit_sha256"] != sha(DATA / "audit-results.json") \
            or result["model_freeze_sha256"] != sha(P7 / "FROZEN.json"):
        raise RuntimeError("Current complete audited frozen results required")
    all_defended_predictions_idle = all(
        row["confusion_true_then_prediction"]["01"] == 0
        and row["confusion_true_then_prediction"]["11"] == 0
        for row in result["results"] if row["arm"] != "shared")
    if not all_defended_predictions_idle:
        raise RuntimeError("The fixed figure caption would contradict the held predictions")
    descriptive = []
    for row in result["results"]:
        folder = DATA / f"{row['product']}-{row['delay_ms']}-{row['arm']}"
        seal = read(folder / "sealed.json")
        audited = next(a for a in audit["cohorts"] if a["run_id"] == seal["run_id"])
        if sha(folder / "sealed.json") != audited["sealed_sha256"] \
                or any(sha(folder / name) != expected for name, expected in seal["sha256"].items()):
            raise RuntimeError("Descriptive input changed after its native audit")
        trials = rows(folder / "trials.jsonl")
        origin = Counter(r["marker"] for r in read(folder / "server.json")["origin_log"] if r.get("marker"))
        active = [t for t in trials if t["active"]]
        primary_receiver_lookups = sum(origin[t["receiver_request"]["marker"]] for t in trials)
        after_fill_lookups = sum(origin[t["receiver_request"]["marker"]] for t in active)
        requests = rows(folder / "requests.jsonl")
        ordinary = [r for r in requests
                    if r["marker"].endswith(("/canary/function-0", "/canary/function-1",
                                            "/canary/function-5"))]
        conditionals = [r for r in requests if r["marker"].endswith("/canary/function-3")]
        ranges = [r for r in requests if r["marker"].endswith("/canary/function-4")]
        denied = [r for r in requests if r["marker"].endswith("/canary/unknown-origin")]
        descriptor = {
            "product": row["product"], "delay_ms": row["delay_ms"], "arm": row["arm"],
            "receiver_trials": len(trials), "actual_sender_fills": len(active),
            "primary_receiver_origin_lookups": primary_receiver_lookups,
            "receiver_origin_lookups_after_fill": after_fill_lookups,
            "sender_filled_receivers_without_origin_lookup": len(active) - after_fill_lookups,
            "ordinary_successful_get_canaries": sum(r["status"] == 200 and r["body_bytes"] == 40
                                                   and r["body_sha256"] == hashlib.sha256(
                                                       b"J controls harmless artifact fixture v1\n").hexdigest()
                                                   for r in ordinary),
            "ordinary_get_canaries": len(ordinary),
            "conditional_statuses": [r["status"] for r in conditionals],
            "range_status_and_body_bytes": [[r["status"], r["body_bytes"]] for r in ranges],
            "unknown_origin_denied": sum(r["status"] == 403 and not origin[r["marker"]] for r in denied),
            "unknown_origin_probes": len(denied),
            "client_body_bytes": row["body_bytes_to_clients"],
            "origin_body_bytes": row["body_bytes_from_origin"],
            "cohort_seal_sha256": audited["sealed_sha256"],
        }
        if descriptor["ordinary_successful_get_canaries"] != descriptor["ordinary_get_canaries"] \
                or descriptor["unknown_origin_denied"] != descriptor["unknown_origin_probes"]:
            raise RuntimeError("Cannot describe failed successful-read or confinement canaries")
        descriptive.append(descriptor)
    summary = {
        "scope": "Descriptive counts from audited records; no additional test, decoder or selection.",
        "cohorts": descriptive,
        "held_trials": sum(r["receiver_trials"] for r in descriptive),
        "all_defended_frozen_predictions_idle": all_defended_predictions_idle,
        "ordinary_successful_get_canaries": sum(r["ordinary_successful_get_canaries"] for r in descriptive),
        "ordinary_get_canaries": sum(r["ordinary_get_canaries"] for r in descriptive),
        "unknown_origin_denied": sum(r["unknown_origin_denied"] for r in descriptive),
        "unknown_origin_probes": sum(r["unknown_origin_probes"] for r in descriptive),
        "actual_client_plus_origin_http_all_calibration_and_holdout":
            audit["global_total_http_requests"]
            + read(P7 / "data/g2-cal/audit-results.json")["global_total_http_requests"],
        "native_binary_image": read(P7 / "build/g2/build-result.json")["readback"],
    }
    with (HERE / "descriptive-counts.json").open("x") as stream:
        json.dump(summary, stream, indent=2)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(figsize=(9.4, 6.2))
    fig.subplots_adjust(left=.21, right=.98, top=.77, bottom=.23)
    arms = ["shared", "tenant-keyed", "private", "disabled", "no-sender"]
    names = ["Shared cache", "Trusted tenant key", "Private instances", "Cache disabled", "No sender"]
    colors = ["#176b87", "#62a1b6", "#bd542c", "#e6a17e"]
    settings = [("nginx", 20), ("nginx", 150), ("squid", 20), ("squid", 150)]
    for offset, ((product, delay), color) in enumerate(zip(settings, colors)):
        selected = [next(r for r in result["results"]
                         if r["product"] == product and r["delay_ms"] == delay and r["arm"] == arm)
                    for arm in arms]
        x = [r["accuracy"] for r in selected]
        y = [i + (offset - 1.5) * .16 for i in range(len(arms))]
        error = [[r["accuracy"] - r["wilson95_accuracy"][0] for r in selected],
                 [r["wilson95_accuracy"][1] - r["accuracy"] for r in selected]]
        ax.errorbar(x, y, xerr=error, fmt="o", capsize=3, markersize=5,
                    linewidth=1.2, color=color, label=f"{product.upper()} · {delay} ms miss delay")
    ax.set(yticks=range(len(arms)), yticklabels=names, xlim=(.2, 1.045),
           ylim=(4.55, -.55), xlabel="Correct held predictions")
    ax.xaxis.set_major_formatter(PercentFormatter(1))
    ax.axvline(.5, color="#999999", linestyle=":", linewidth=1)
    ax.grid(axis="x", alpha=.18)
    ax.legend(loc="upper center", bbox_to_anchor=(.5, 1.28), ncol=2, frameon=False)
    fig.suptitle("Frozen cache timing decoders on fresh held trials", x=.5, y=.98,
                 fontsize=14, fontweight="bold")
    fig.text(.02, .035,
             "32 trials per cache arm; 16 per no-sender arm. Wilson intervals describe this shared-host run.\n"
             "All four shared controls pass the frozen Holm test. Each defended decoder always predicts idle;\n"
             "native origin joins establish the tested application-cache intervention. General timing isolation remains unmeasured.",
             fontsize=9, color="#555555")
    for extension in ("png", "pdf", "svg"):
        target = HERE / f"P7-holdout.{extension}"
        if target.exists():
            raise RuntimeError("Preserve earlier figure")
        fig.savefig(target, dpi=200, bbox_inches="tight")
    plt.close(fig)
    inputs = {
        "held_results": DATA / "holdout-results.json",
        "held_native_audit": DATA / "audit-results.json",
        "retained_remote_native_audit": DATA / "remote-audit-results.json",
        "model_freeze": P7 / "FROZEN.json",
        "scientific_source_freeze": P7 / "SOURCES-FROZEN.json",
        "operational_amendment_freeze": EVIDENCE / "P7-audit-recovery/AMENDMENT-FROZEN.json",
        "transport_manifest": EVIDENCE / "VM2/P7-G2-MAIN-TRANSPORT.json",
        "final_runtime_readback": EVIDENCE / "VM2/P7-G2-FINAL-RUNTIME-READBACK.json",
    }
    provenance = {"source_sha256": sha(Path(__file__)),
                  "inputs": {name: {"path": str(path.relative_to(EVIDENCE.parent)),
                                   "sha256": sha(path)} for name, path in inputs.items()},
                  "outputs_sha256": {path.name: sha(path) for path in HERE.iterdir()
                                     if path.suffix in {".png", ".pdf", ".svg"}
                                     or path.name == "descriptive-counts.json"},
                  "statistical_rule": result["limitations"]}
    with (HERE / "provenance.json").open("x") as stream:
        json.dump(provenance, stream, indent=2)
    print(json.dumps({"plotted_cohorts": 20, "held_trials": summary["held_trials"],
                      "ordinary_get_canaries": summary["ordinary_get_canaries"],
                      "unknown_origin_probes": summary["unknown_origin_probes"],
                      "provenance": str(HERE / "provenance.json")}, indent=2))


if __name__ == "__main__":
    main()
