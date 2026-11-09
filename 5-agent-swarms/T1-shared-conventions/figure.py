"""T1 figure: pair-class rates with bootstrap intervals, and sender->receiver matrices per context."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parents[1] / "reports" / "figures"
a = json.loads((ROOT / "main" / "analysis.json").read_text())
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SERIES = {"same_model": "#2a78d6", "same_developer": "#eb6834", "cross_developer": "#1baf7a"}
LABEL = {"same_model": "Same model", "same_developer": "Same developer,\ndifferent model", "cross_developer": "Different developer"}
RAMP = LinearSegmentedColormap.from_list("blue", ["#fcfcfb", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
MODELS = ["luna", "oss120b", "deepseek", "mistral", "llama4", "glm5", "qwen3"]
NAMES = ["Luna", "gpt-oss", "DeepSeek", "Mistral", "Llama 4", "GLM 5", "Qwen3"]
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2})
fig = plt.figure(figsize=(7.2, 2.7))
gs = fig.add_gridspec(1, 3, width_ratios=[1.05, 1, 1], wspace=0.42)
ax = fig.add_subplot(gs[0])
groups = [("C0", "Bare server"), ("C1", "Package mirror")]
TICK = ["Bare\nserver", "Package\nmirror"]
for gi, (ctx, gname) in enumerate(groups):
    m = a["metrics"][f"full|{ctx}"]
    for ci, cls in enumerate(SERIES):
        v = m[f"same|{cls}"]
        x = gi + (ci - 1) * 0.22
        ax.errorbar(x, v["estimate"], yerr=[[v["estimate"] - v["ci95"][0]], [v["ci95"][1] - v["estimate"]]],
                    fmt="o", ms=5, color=SERIES[cls], ecolor=SERIES[cls], elinewidth=1.5, capsize=0,
                    label=LABEL[cls] if gi == 0 else None, zorder=3)
ax.set_xticks([0, 1], TICK)
ax.set_xlim(-0.5, 1.5); ax.set_ylim(0, 1.05)
ax.set_ylabel("Fully compatible pairs (16/16 values)")
ax.yaxis.grid(True, color=GRID, lw=0.6); ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
ax.legend(frameon=False, fontsize=6.5, loc="upper left", handletextpad=0.3, borderaxespad=0.1)
ax.set_title("A. Same wording, 95% bootstrap CI", loc="left", fontsize=8, color=INK)
for k, (ctx, gname) in enumerate(groups):
    axh = fig.add_subplot(gs[k + 1])
    mat = a["matrix_full"][f"same|{ctx}"]
    grid = [[mat[f"{s}->{r}"] for r in MODELS] for s in MODELS]
    axh.imshow(grid, cmap=RAMP, vmin=0, vmax=1)
    for i in range(7):
        for j in range(7):
            v = grid[i][j]
            axh.text(j, i, f"{v:.2f}".lstrip("0") if v < 1 else "1", ha="center", va="center", fontsize=5.5,
                     color="#ffffff" if v > 0.55 else INK)
    axh.set_xticks(range(7), NAMES, rotation=60, ha="right", fontsize=6)
    axh.set_yticks(range(7), NAMES if k == 0 else [""] * 7, fontsize=6)
    axh.set_xlabel("Receiver", fontsize=7)
    if k == 0:
        axh.set_ylabel("Sender", fontsize=7)
    axh.set_title(f"{'BC'[k]}. {gname}", loc="left", fontsize=8, color=INK)
    for s in axh.spines.values():
        s.set_visible(False)
    axh.tick_params(length=0)
OUT.mkdir(parents=True, exist_ok=True)
for ext in ("png", "pdf"):
    fig.savefig(OUT / f"T1-convention.{ext}", dpi=220, bbox_inches="tight", facecolor="#fcfcfb")
print("saved", OUT / "T1-convention.png")
