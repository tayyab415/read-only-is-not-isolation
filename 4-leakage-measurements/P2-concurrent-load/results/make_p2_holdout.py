"""Plot the four original P2 held scores and their reported intervals."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent
P2 = LAB / "evidence/P2-residual"
VERIFY = LAB / "evidence/P2-verification"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    destination = HERE / "P2-holdout-v2"
    destination.mkdir(exist_ok=False)
    primary_path = P2 / "results/p2-m1/holdout-results.json"
    primary = json.loads(primary_path.read_text())
    verification = json.loads((VERIFY / "RESULT.json").read_text())
    native_path = P2 / "data/p2-m1/holdout-audit-results.json"
    native = json.loads(native_path.read_text())
    if not native["passed"] or not verification["passed"] \
            or not verification["primary_aa_null_passed"] \
            or verification["holdout_results_sha256"] != sha(primary_path):
        raise RuntimeError("The complete original held result and independent null are required")
    arms = ("separate", "sham", "aa", "immutable")
    values = [primary["results"][arm] for arm in arms]
    if any(row["trials"] != 1000 or row["accuracy"] != row["correct"] / 1000
           for row in values):
        raise RuntimeError("All original 1,000-trial denominators are required")
    scores = np.array([row["accuracy"] for row in values])
    intervals = np.array([row["binomial_interval_98_75"] for row in values])
    error = np.vstack((scores - intervals[:, 0], intervals[:, 1] - scores))
    labels = ["Separate\nrepositories", "Concurrent\ndummy service",
              "A/A\nalways idle", "Private immutable\nserving"]
    colors = ["#356a9a", "#757ca3", "#86959c", "#aa7659"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    figure, axis = plt.subplots(figsize=(9.2, 5.8))
    figure.subplots_adjust(left=.10, right=.97, top=.79, bottom=.28)
    x = np.arange(4)
    axis.bar(x, scores * 100, width=.58, color=colors, alpha=.94)
    axis.errorbar(x, scores * 100, yerr=error * 100, fmt="none",
                  ecolor="#24333f", capsize=5, linewidth=1.5)
    axis.axhline(50, color="#55616a", linestyle="--", linewidth=1.2)
    axis.set_ylim(0, 105)
    axis.set_yticks(range(0, 101, 25))
    axis.set_ylabel("Assigned-label accuracy (%)")
    axis.set_xticks(x, labels)
    axis.grid(axis="y", alpha=.15)
    axis.set_axisbelow(True)
    for index, row in enumerate(values):
        axis.text(index, intervals[index, 1] * 100 + 2.8,
                  f"{row['correct']:,}/1,000", ha="center", fontweight="bold", fontsize=11)
    figure.text(.10, .94, "Activity remains observable under repository separation",
                fontsize=16, fontweight="bold")
    figure.text(.10, .885, "P2 fresh holdouts, four separately frozen receiver classifiers",
                fontsize=11, color="#40505e")
    figure.text(.10, .15,
                "The A/A primary null passes. Concurrent dummy load is not a zero-load null.\n"
                "Intervals are the original 98.75% binomial intervals; independence is an assumption.\n"
                "Scores do not identify a cause, prove model communication or measure capacity.",
                fontsize=9.5, color="#40505e", linespacing=1.6, va="top")
    for suffix in ("png", "pdf", "svg"):
        figure.savefig(destination / f"P2-holdout.{suffix}", dpi=180, facecolor="white")
    plt.close(figure)
    inputs = [primary_path, native_path, P2 / "SOURCES-FROZEN.json", P2 / "FROZEN.json",
              VERIFY / "VERIFICATION-FROZEN.json", VERIFY / "RESULT.json"]
    provenance = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {str(path.relative_to(LAB)): sha(path) for path in inputs},
        "generator_sha256": sha(Path(__file__)),
        "plotted": {arm: {"correct": row["correct"], "trials": row["trials"],
                          "accuracy": row["accuracy"],
                          "original_interval_98_75": row["binomial_interval_98_75"]}
                    for arm, row in zip(arms, values)},
        "primary_aa_null_passed": True,
        "statistics_recomputed_or_changed": False,
        "outputs": {path.name: sha(path) for path in sorted(destination.iterdir())},
        "native_requests": 0, "provider_calls": 0,
    }
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"outputs": list(provenance["outputs"]), "trials": 4000}))


if __name__ == "__main__":
    main()
