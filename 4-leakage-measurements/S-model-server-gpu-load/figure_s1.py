"""S1 figure: holdout decoding accuracy per arm, and utility per cache policy."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parents[1] / "reports" / "figures"
a = json.loads((ROOT / "analysis.json").read_text())
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
C_CACHE, C_LOAD = "#2a78d6", "#eb6834"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2})
arms = [("shared", "Shared cache", C_CACHE), ("client_salt", "Client-chosen salt", C_CACHE),
        ("shared_short", "Shared, ~100-token prefix", C_CACHE), ("shared_busy", "Shared, busy server", C_CACHE),
        ("salted", "Gateway salt", C_CACHE), ("salted_busy", "Gateway salt, busy server", C_CACHE),
        ("nocache", "Prefix caching off", C_CACHE),
        ("contention_light", "Gateway salt; one ordinary request", C_LOAD),
        ("contention", "Gateway salt; one long request", C_LOAD)]
fig = plt.figure(figsize=(7.2, 2.9))
gs = fig.add_gridspec(1, 2, width_ratios=[1.35, 1], wspace=0.55)
ax = fig.add_subplot(gs[0])
for k, (arm, label, col) in enumerate(arms):
    r = a[arm]
    y = len(arms) - 1 - k
    lo, hi = r["wilson95"]
    ax.plot([lo, hi], [y, y], color=col, lw=1.6, solid_capstyle="round")
    ax.plot(r["accuracy"], y, "o", color=col, ms=5, zorder=3)
    ax.text(1.045, y, f"{r['accuracy']:.2f}", va="center", fontsize=6.5, color=INK2)
ax.axvline(0.5, color=INK2, lw=0.8, ls=(0, (3, 2)))
ax.set_yticks(range(len(arms)), [x[1] for x in arms][::-1], fontsize=6.8)
ax.set_xlim(0.4, 1.02); ax.set_xticks([0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
ax.set_xlabel("Holdout accuracy of frozen latency threshold (400 trials, Wilson 95% CI)")
ax.xaxis.grid(True, color=GRID, lw=0.6); ax.set_axisbelow(True)
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.tick_params(axis="y", length=0)
ax.plot([], [], "o", color=C_CACHE, label="Sender changes cache state")
ax.plot([], [], "o", color=C_LOAD, label="Sender only adds load")
ax.legend(frameon=False, fontsize=6.3, loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=2, handletextpad=0.2)
ax.set_title("A. Can tenant B read tenant A's activity?", loc="left", fontsize=8, color=INK, pad=16)
ax2 = fig.add_subplot(gs[1])
cfgs = [("utility|cache_on", "shared", "Shared"), ("utility|cache_on", "salted", "Gateway salt"), ("utility|cache_off", "nocache", "Caching off")]
for gi, wl in enumerate(("distinct", "common")):
    for ci, (ph, cfg, label) in enumerate(cfgs):
        v = a[ph][f"{cfg}|{wl}"]
        x = gi * 4 + ci
        col = ["#2a78d6", "#1baf7a", "#eb6834"][ci]
        ax2.bar(x, v["mean_ms"]["mean"], width=0.8, color=col, label=label if gi == 0 else None)
        ax2.errorbar(x, v["mean_ms"]["mean"], yerr=v["mean_ms"]["sd"], color=INK2, lw=0.8, capsize=0)
        ax2.text(x, v["mean_ms"]["mean"] + 30, f"{v['req_per_s']['mean']:.1f}/s", ha="center", fontsize=5.8, color=INK2)
ax2.set_xticks([1, 5], ["Own scaffold\nper tenant", "Scaffold shared\nby both tenants"])
ax2.set_ylabel("Mean request latency, ms (3 reps, ±sd)")
ax2.set_ylim(0, 1650)
ax2.yaxis.grid(True, color=GRID, lw=0.6); ax2.set_axisbelow(True)
for s in ("top", "right"):
    ax2.spines[s].set_visible(False)
ax2.legend(frameon=False, fontsize=6.0, loc="upper left", ncol=3, columnspacing=0.6, handlelength=1.0, handletextpad=0.3)
ax2.set_title("B. Cost to ordinary agent traffic", loc="left", fontsize=8, color=INK, pad=16)
OUT.mkdir(parents=True, exist_ok=True)
for ext in ("png", "pdf"):
    fig.savefig(OUT / f"S1-model-server.{ext}", dpi=220, bbox_inches="tight", facecolor="#fcfcfb")
print("saved")
