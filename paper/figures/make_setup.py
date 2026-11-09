# Methods overview: threat model, setup and build steps (submission Figure 1).
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BLUE, RED, GREEN, GREY = "#2b5c8a", "#b03a2e", "#2e7d32", "#7f8c8d"
fig, ax = plt.subplots(figsize=(10, 4.3))
ax.set_xlim(0, 100); ax.set_ylim(0, 46); ax.axis("off")

def box(x, y, w, h, text, ec, fc="white", fs=9, bold=False, ls="-"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3", ec=ec, fc=fc, lw=1.6, ls=ls))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal")

def arrow(x1, y1, x2, y2, c, ls="-", style="-|>"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=12, color=c, lw=1.5, ls=ls))

# sandboxes
box(1, 30, 17, 9, "Sandbox A (Alice)\nown tools, files, code\nread-only account", BLUE, fs=8.5)
box(1, 13, 17, 9, "Sandbox B (Bob)\nown tools, files, code\nread-only account", BLUE, fs=8.5)
ax.plot([9.5, 9.5], [23.5, 28.5], ls=":", color=GREY, lw=2)
ax.text(10.5, 26, "no shared files,\nno network route", fontsize=7.5, color=GREY, va="center")

# gateway
box(25, 17, 15, 17, "Gateway\n\nGET / HEAD only\n\nbinds network\naddress to account\n(double door)", GREY, fs=8.5)
arrow(18.6, 34, 24.6, 29, BLUE); arrow(18.6, 17, 24.6, 22, BLUE)
ax.text(19.2, 36, "A asks", fontsize=8, color=BLUE); ax.text(19.2, 13.5, "B asks", fontsize=8, color=BLUE)

# shared service
ax.add_patch(FancyBboxPatch((46, 6), 31, 37, boxstyle="round,pad=0.3", ec=RED, fc="#fdf2f0", lw=1.6))
ax.text(61.5, 41, "One shared service", ha="center", fontsize=10, fontweight="bold")
box(48, 31.5, 27, 6.5, "Package mirror: Artifactory 7.161.26\nor model server: vLLM 0.33.0, one L4", GREY, fs=7.3)
box(48, 20, 27, 9, "State that reads change\ndownload counter,\nremembered misses, prefix cache", RED, fc="#f8d7d3", fs=7.6)
box(48, 8.5, 27, 9, "Shared resources\nload on CPU, GPU, queues", RED, ls="--", fs=8)
arrow(40.6, 25.5, 47.4, 25.5, GREY)

# upstream
box(83, 20, 15, 11, "Local upstream\n(mirror only;\nfixed 150 or\n20 ms delay)", GREY, fs=8.5)
arrow(77.6, 25.5, 82.6, 25.5, GREY)

# the leak
ax.annotate("A's read changes shared state;\nB's later read observes it = 1 bit",
            xy=(48, 24), xytext=(20, 3.5), fontsize=8.5, color=RED,
            arrowprops=dict(arrowstyle="-|>", color=RED, lw=1.4))

# build steps
steps = "Build:  1 rebuild the board  >  2 harden sandboxes (72/72)  >  3 attack scripts  >  4 control matrix  >  5 close what is left (9 records)  >  6 models and agents"
ax.text(50, 0.2, steps, ha="center", fontsize=7.6, color="#333333")
plt.savefig(__file__.replace("make_setup.py", "setup.pdf"), bbox_inches="tight")
plt.savefig(__file__.replace("make_setup.py", "setup.png"), bbox_inches="tight", dpi=130)
