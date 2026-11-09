"""Prospective P8 architecture figure. No measured P8 result is depicted."""
from pathlib import Path
import hashlib
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent
SOURCE = LAB / "evidence/P8-telemetry/SOURCES-FROZEN.json"
SOURCE_REPORT = LAB / "reports/P8-telemetry-sources.md"
fig, ax = plt.subplots(figsize=(12.0, 7.7))
fig.patch.set_facecolor("white")
ax.set_xlim(0, 12)
ax.set_ylim(0, 7.7)
ax.axis("off")


def box(x, y, width, height, color, edge="#657083", radius=.12):
    patch = FancyBboxPatch((x, y), width, height,
                          boxstyle=f"round,pad=0.03,rounding_size={radius}",
                          facecolor=color, edgecolor=edge, linewidth=1.15)
    ax.add_patch(patch)


def text(x, y, value, size=11, color="#243045", weight="normal", align="center"):
    ax.text(x, y, value, ha=align, va="center", fontsize=size, color=color,
            weight=weight, linespacing=1.45)


def arrow(start, end, color="#476B96", style="-", width=1.8):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13,
                                color=color, linewidth=width, linestyle=style))


text(6, 7.34, "Private files and networks leave a separate telemetry question", 17, weight="bold")
text(6, 6.94, "P8 architecture under test. Native collection has not run.", 10, "#596579")
box(.2, .9, 11.6, 5.65, "#F7F8FA", "#8D98A8")
text(6, 6.25, "One owned VM, one shared Linux kernel", 12, weight="bold")

for x, actor in ((.55, "Alice"), (8.55, "Bob")):
    box(x, 3.6, 2.9, 2.18, "#EEF4FB", "#476B96")
    text(x + 1.45, 5.5, actor + " sandbox", 13, weight="bold")
    text(x + 1.45, 5.02, "Private PID, network, mount\nand cgroup namespace views", 10)
    box(x + .18, 3.85, 2.54, .57, "white", "#9CAAC0")
    text(x + 1.45, 4.13, "Writable private /tmp\n1 KiB file write + readback", 9.5)
    box(x, 1.42, 2.9, 1.03, "white", "#476B96")
    text(x + 1.45, 2.11, "Private immutable mirror", 11, weight="bold")
    text(x + 1.45, 1.75, "Approved GET, same 31-byte fixture", 9.5)
    arrow((x + 1.45, 3.53), (x + 1.45, 2.53))
    text(x + 1.65, 3.0, "HTTP before + after\ncounter window", 9.0, align="left")

text(2, 4.58, "Fixed CPU sender, 0.5 CPU quota", 9.5)
text(10, 4.58, "Fixed reader, 0.5 CPU quota", 9.5)

box(4.24, 4.58, 3.52, 1.13, "#FFF3DE", "#B98428")
text(6, 5.37, "Host aggregate CPU accounting", 11, weight="bold")
text(6, 4.93, "Native /proc/stat CPU row\nincludes work outside Bob", 10)
arrow((3.52, 5.13), (4.15, 5.13), "#B98428")
text(3.84, 5.6, "CPU work", 9.0, "#805B1A")
arrow((7.84, 5.13), (8.46, 5.13), "#B98428")
text(8.12, 5.6, "Native view", 9.0, "#805B1A")

box(4.62, 3.3, 2.76, .8, "#EAF1EB", "#65846A")
text(6, 3.79, "Static file bound read-only", 10.5, weight="bold")
text(6, 3.49, "Masked view at the same pathname", 9.2)
arrow((7.46, 3.72), (8.47, 4.48), "#65846A", "--")
text(8.0, 3.68, "One-file\nintervention", 9.0, "#49664F")

text(6, 2.69, "Other host processes also contribute to the live counter.\nA trusted host sampler checks the accounting scope.", 10)
text(6, 1.8, "Both views retain private files and approved HTTP access.\nIdle-sender controls run under both counter views.", 10, weight="bold")

text(6, .55, "CPU quotas limit consumption; they do not rewrite aggregate accounting.\nMasking one counter input does not establish timing isolation.", 10.5)
fig.subplots_adjust(left=.015, right=.985, top=.985, bottom=.02)

for extension in ("png", "pdf", "svg"):
    path = HERE / f"P8-architecture.{extension}"
    if path.exists():
        raise RuntimeError("Preserve the previous architecture artifact before replacing it")
    fig.savefig(path, dpi=240, facecolor="white")
plt.close(fig)

provenance = {
    "conceptual_architecture": True,
    "measured_P8_results_depicted": False,
    "scope": "Prospective shell-enabled observer; CPU row only, no general timing-isolation claim",
    "source_freeze_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    "primary_source_report_sha256": hashlib.sha256(SOURCE_REPORT.read_bytes()).hexdigest(),
    "figure_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "primary_source_locators": ["K2 show_stat lines 82-188", "K3 cpu.stat/cpu.max lines 1092-1144",
                                "M1 default masking lines 193-220", "O1 masking/read-only lines 898-914"],
}
(HERE / "P8-architecture-provenance.json").write_text(json.dumps(provenance, indent=2))
