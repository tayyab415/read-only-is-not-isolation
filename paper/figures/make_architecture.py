"""Figure: shared pull-through cache vs frozen admission with private serving. Conceptual, no data."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BLUE, RED, GREY, GREEN = "#2b5d8a", "#b03a2e", "#7f8c8d", "#2e7d32"


def box(ax, x, y, w, h, text, color, fill="white", size=9, bold=False, ls="-"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.04",
                                lw=1.4, ec=color, fc=fill, ls=ls))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color="black",
            weight="bold" if bold else "normal", wrap=True)


def arrow(ax, a, b, color="black", ls="-", text=None, off=(0, 0.05), size=8):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=11, lw=1.3, color=color, ls=ls))
    if text:
        ax.text((a[0] + b[0]) / 2 + off[0], (a[1] + b[1]) / 2 + off[1], text, fontsize=size, color=color,
                ha="center", va="center")


fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
for ax in axes:
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

# Left: today
ax = axes[0]
ax.set_title("Today: one shared pull-through cache", fontsize=11, weight="bold")
box(ax, 0.2, 4.2, 2.0, 1.0, "Sandbox A\n(read-only)", BLUE)
box(ax, 0.2, 0.8, 2.0, 1.0, "Sandbox B\n(read-only)", BLUE)
ax.plot([1.2, 1.2], [2.0, 4.0], color=GREY, lw=2, ls=":")
ax.text(1.35, 3.0, "no direct\nroute", fontsize=8, color=GREY, va="center")
box(ax, 3.0, 2.5, 1.5, 1.0, "Shared\ngateway", GREY)
box(ax, 5.2, 0.6, 3.0, 4.8, "", RED, fill="#fdf2f0")
ax.text(6.7, 5.1, "Artifactory remote repo", ha="center", fontsize=9, weight="bold")
box(ax, 5.45, 3.8, 2.5, 0.8, "Content (bytes)\nreads can't change", GREY, size=8)
box(ax, 5.45, 2.4, 2.5, 1.1, "Read-triggered records\ncount, last downloader,\nremembered 404", RED, fill="#f9d9d4", size=8)
box(ax, 5.45, 1.0, 2.5, 1.1, "Shared resources\nCPU, queues, DB", RED, size=8, ls="--")
box(ax, 8.6, 2.5, 1.3, 1.0, "Upstream\n(PyPI)", GREY, size=8)
arrow(ax, (2.2, 4.7), (3.0, 3.25), BLUE, text="A reads", off=(-0.55, 0.0))
arrow(ax, (2.2, 1.3), (3.0, 2.75), BLUE, text="B reads", off=(-0.55, 0.0))
arrow(ax, (4.5, 3.0), (5.2, 3.0), GREY)
arrow(ax, (8.2, 3.0), (8.6, 3.0), GREY)
ax.annotate("A's read changes a record;\nB's later read observes it", xy=(5.45, 2.9), xytext=(2.4, 0.15),
            fontsize=8, color=RED, arrowprops=dict(arrowstyle="-|>", color=RED, lw=1.1))

# Right: proposed
ax = axes[1]
ax.set_title("Proposed: frozen admission, private immutable serving", fontsize=11, weight="bold")
box(ax, 0.2, 4.4, 1.6, 1.0, "Upstream\n(PyPI)", GREY, size=8)
box(ax, 2.4, 4.4, 3.2, 1.0, "Trusted admission\n(before the run)\nresolve, verify hashes", GREEN, fill="#eaf5ea", size=8)
box(ax, 6.2, 4.4, 2.2, 1.0, "Frozen catalog\n24 wheels, 5.0 MB", GREEN, size=8)
arrow(ax, (1.8, 4.9), (2.4, 4.9), GREY)
ax.text(2.1, 5.55, "once", fontsize=8, color=GREY, ha="center")
arrow(ax, (5.6, 4.9), (6.2, 4.9), GREEN)
box(ax, 4.6, 2.3, 2.3, 1.0, "Private server A\nread-only copy", GREEN, size=8)
box(ax, 4.6, 0.5, 2.3, 1.0, "Private server B\nread-only copy", GREEN, size=8)
box(ax, 0.6, 2.3, 2.2, 1.0, "Sandbox A", BLUE)
box(ax, 0.6, 0.5, 2.2, 1.0, "Sandbox B", BLUE)
arrow(ax, (6.8, 4.4), (5.9, 3.3), GREEN)
arrow(ax, (7.5, 4.4), (6.95, 1.35), GREEN)
arrow(ax, (2.8, 2.8), (4.6, 2.8), BLUE, text="origin-bound route", off=(0, 0.25))
arrow(ax, (2.8, 1.0), (4.6, 1.0), BLUE)
box(ax, 7.75, 0.5, 2.15, 2.8, "Still shared:\nhost CPU, memory\nbandwidth, kernel\ncounters\n(separate problem:\ntime protection)", GREY, size=8, ls="--")
ax.text(5.0, 0.1, "Unknown package -> local failure, not a shared upstream fetch", fontsize=8, ha="center", color=GREEN)

fig.tight_layout()
out = Path(__file__).resolve().parent
fig.savefig(out / "architecture.pdf")
fig.savefig(out / "architecture.png", dpi=150)
print("ok")
