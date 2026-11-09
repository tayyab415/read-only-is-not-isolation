"""P1 per-recipe heatmap from the frozen holdout results. Reads only report-metrics.json."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SRC = Path(__file__).resolve().parents[3] / "evidence/P1-meter/results/analysis-v2/main-m1/report-metrics.json"
m = json.loads(SRC.read_text())
arms = ["open", "combined", "separate", "aa", "sham"]
arm_labels = ["Shared, open", "Stats denied +\nmisses uncached", "Separate repos +\norigin binding", "A/A (idle sender)", "Sham (dummy target)"]
recipes = ["get", "range_get", "load", "rdv_get", "rdv_head", "head", "cond_get", "stats", "properties", "folder_html", "folder_api", "fresh_get"]
recipe_labels = ["GET", "Range GET\n(1 byte)", "Load burst", "GET missing\nrendezvous", "HEAD missing\nrendezvous", "HEAD", "Conditional\nGET", "Read stats", "Read\nproperties", "Folder\nHTML", "Folder\nAPI", "GET fresh\nmissing"]
dec = {(d["run"], d["recipe"]): d for d in m["recipe_decoders"]}
conf = {}
for c in m["confirmations"]:
    if c["recipe"] and c["p_holm_experiment"] < 0.05:
        conf[(c["run"], c["recipe"])] = conf.get((c["run"], c["recipe"]), 0) + 1
grid = np.array([[dec[(a, r)]["balanced_accuracy"] for r in recipes] for a in arms])
fig, ax = plt.subplots(figsize=(10, 3.9))
im = ax.imshow(grid, cmap="RdBu_r", vmin=0.25, vmax=0.75, aspect="auto")
for i, a in enumerate(arms):
    for j, r in enumerate(recipes):
        sig = dec[(a, r)]["p_holm_experiment"] < 0.05
        mark = ("*" if sig else "") + ("†" if (a, r) in conf else "")
        ax.text(j, i, f"{grid[i, j]:.2f}{mark}", ha="center", va="center", fontsize=9.5,
                color="white" if abs(grid[i, j] - 0.5) > 0.17 else "black")
ax.set_xticks(range(len(recipes)), recipe_labels, fontsize=9)
ax.set_yticks(range(len(arms)), arm_labels, fontsize=9.5)
ax.axvline(4.5, color="black", lw=1)
ax.text(2, -0.85, "change shared state", ha="center", fontsize=8)
ax.text(8, -0.85, "do not change counters or miss memory", ha="center", fontsize=8)
cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
cb.set_label("Balanced accuracy", fontsize=8)
fig.text(0.01, 0.01, "Frozen pooled decoder on 1,000 sealed holdout trials per arm; ~30-57 active trials per recipe vs 500 idle. "
         "* decoder Holm p<0.05 (experiment-wide); † a calibration-selected feature confirms for this recipe.", fontsize=7)
fig.tight_layout(rect=(0, 0.04, 1, 1))
fig.savefig(Path(__file__).with_name("p1-recipes.pdf"))
fig.savefig(Path(__file__).with_name("p1-recipes.png"), dpi=160)
print("ok")
