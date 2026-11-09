"""Export the original verified P8 held scores and intervals without recomputation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent
EVIDENCE = LAB / "evidence"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    study = EVIDENCE / "P8-telemetry"
    primary_path = study / "results/p8-m1/holdout-results.json"
    primary = json.loads(primary_path.read_text())
    verification_path = EVIDENCE / "P8-verification/RESULT.json"
    verification = json.loads(verification_path.read_text())
    assert verification["passed"] and verification["primary_aa_null_passed"]
    assert verification["holdout_results_sha256"] == sha(primary_path)
    assert primary["null_gate_passed"]
    output = HERE / "P8-holdout-v2"
    output.mkdir(exist_ok=False)
    cells = ("native", "masked", "aa-native", "aa-masked")
    labels = ("Live host counter", "Static file over counter",
              "Idle control, live counter", "Idle control, static file")
    colors = ("#2059a8", "#34836f", "#707780", "#707780")
    fig, ax = plt.subplots(figsize=(8.7, 5.4))
    fig.subplots_adjust(left=.29, right=.95, top=.79, bottom=.34)
    for index, cell in enumerate(cells):
        row = primary["results"][cell]
        value = row["accuracy"]
        low, high = row["accuracy_99pct_iid_binomial_interval"]
        ax.errorbar(value, 3 - index, xerr=[[value - low], [high - value]],
                    fmt="o", capsize=4, color=colors[index], markersize=8, linewidth=2)
        ax.text(.985, 3 - index + .13, f"{row['correct']}/64", ha="right",
                fontsize=10, color=colors[index])
    ax.axvline(.5, color="#959ca4", linestyle="--", linewidth=1)
    ax.set(xlim=(0, 1.03), ylim=(-.55, 3.55),
           yticks=range(4), yticklabels=list(reversed(labels)),
           xlabel="Held activity classification accuracy")
    ax.set_xticks((0, .25, .5, .75, 1), ("0%", "25%", "50%", "75%", "100%"))
    ax.grid(axis="x", alpha=.16)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    fig.text(.07, .94, "Private cgroups still expose the host CPU counter",
             fontsize=16, fontweight="bold", ha="left")
    fig.text(.07, .875, "P8: original frozen reader, 64 fresh held trials per configuration",
             fontsize=11, color="#505860")
    fig.text(.07, .06,
             "Bars are the original 99% IID binomial intervals; shared-host dependence remains.\n"
             "Native Holm p = 0.0004. Both idle controls pass the primary and exact sensitivity families.\n"
             "The static bind removes this tested input. Other telemetry and timing channels remain untested.",
             fontsize=9.3, color="#505860", linespacing=1.6, va="bottom")
    for extension in ("png", "svg", "pdf"):
        fig.savefig(output / ("P8-held." + extension), dpi=180, facecolor="white")
    plt.close(fig)
    provenance = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "primary_results_sha256": sha(primary_path),
        "model_freeze_sha256": sha(study / "FROZEN.json"),
        "held_native_audit_sha256": sha(study / "data/p8-m1/holdout-audit-results.json"),
        "verification_result_sha256": sha(verification_path),
        "complete_review_sha256": sha(EVIDENCE / "P8-review/RESULT.json"),
        "generator_sha256": sha(Path(__file__)), "intervals_recomputed": False,
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha(path)}
                  for path in sorted(output.iterdir())},
        "scope": "Original scores/intervals only; no scientific source, data or primary output changed."}
    with (output / "provenance.json").open("x") as stream:
        json.dump(provenance, stream, indent=2)
    print(json.dumps({"exported": list(provenance["files"]), "original_results_preserved": True}))


if __name__ == "__main__":
    main()
