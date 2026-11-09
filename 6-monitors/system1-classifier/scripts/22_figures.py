"""Paper figures, read only from results/*.json. Palette: dataviz reference categorical slots 1-4 (validated, light mode).

Usage: python3 22_figures.py RESULTS_DIR OUT_DIR
"""
import json
import os
import sys

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

R, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.facecolor": SURFACE, "figure.facecolor": SURFACE, "axes.spines.top": False, "axes.spines.right": False})
J = lambda f: json.load(open(f"{R}/{f}"))

# ---------- Figure 1: unseen-family FPR vs number of families in training ----------
fc = pd.DataFrame(J("family_curve.json")["runs"])
names = {"luna": "GPT-6 Luna (OpenAI)", "glm5": "GLM 5", "llama4": "Llama 4 Maverick", "mistral": "Pixtral Large (Mistral)"}
colors = {"luna": BLUE, "glm5": ORANGE, "llama4": AQUA, "mistral": YELLOW}
markers = {"luna": "o", "glm5": "s", "llama4": "^", "mistral": "D"}
fig, ax = plt.subplots(figsize=(5.2, 3.3))
ax.grid(axis="y", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ends = {}
for fam in ["luna", "glm5", "llama4", "mistral"]:
    g = fc[fc.held_out_family == fam].groupby("k")["held_fpr"].agg(["mean", "std"])
    ax.plot(g.index, 100 * g["mean"], color=colors[fam], linewidth=2, marker=markers[fam], markersize=6, markeredgecolor=SURFACE, markeredgewidth=1.5, label=names[fam], zorder=3)
    ends[fam] = 100 * g["mean"].iloc[-1]
placed = []
for fam, v in sorted(ends.items(), key=lambda kv: kv[1]):  # bottom-up, at least 6 points apart
    yv = max(v, placed[-1] + 6) if placed else v
    placed.append(yv)
    ax.annotate(f"{names[fam]}  {v:.1f}%", xy=(3, v), xytext=(3.12, yv), va="center", fontsize=7.5, color=INK,
                arrowprops=dict(arrowstyle="-", color=INK2, linewidth=0.5) if abs(yv - v) > 1 else None)
m = fc.groupby("k")["held_fpr"].mean()
ax.plot(m.index, 100 * m, color=INK, linewidth=1.2, linestyle=(0, (4, 2)), label="mean of 4", zorder=2)
ax.axhline(1, color=INK2, linewidth=0.8, linestyle=":")
ax.text(0.02, 2.5, "1% operating point (validation FPR)", fontsize=7.5, color=INK2)
ax.set_xticks([0, 1, 2, 3])
ax.set_xlim(-0.15, 4.6)
ax.set_ylim(0, 105)
ax.set_xlabel("Other model families' benign agent scripts in training")
ax.set_ylabel("Benign scripts of the unseen family\nflagged (%)")
ax.set_title("A per-call monitor trained on Swarm Traces flags benign\nagent code from model families it has not seen", fontsize=9.5, color=INK, loc="left")
ax.legend(frameon=False, fontsize=7.5, loc="upper right", bbox_to_anchor=(1.0, 0.93))
fig.text(0.01, 0.005, "TF-IDF + LR on the detok view; 3 seeds x every subset of the other families; source: results/family_curve.json", fontsize=6.5, color=INK2)
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig(f"{OUT}/fig_family_curve.png", dpi=220)
fig.savefig(f"{OUT}/fig_family_curve.pdf")

# ---------- Figure 2: every monitor flags the swarm and benign agent scripts alike ----------
def runs_mean(f, model, col):
    d = pd.DataFrame(J(f)["runs"])
    return float(d[d.model == model][col].mean())

rows = []
for label, f, model in [("TF-IDF (detok)", "gate_v1b.json", "tfidf:detok"), ("TF-IDF (struct)", "gate_v1b.json", "tfidf:struct"),
                        ("Prompt Guard 2 22M, fine-tuned", "encoder_pg2_22m_v1.json", "encoder:v1-pg2-22M-detok"),
                        ("Prompt Guard 2 86M, fine-tuned", "encoder_pg2_86m_v1.json", "encoder:v1-pg2-86M-detok")]:
    rows.append((label, runs_mean(f, model, "test_tpr@thr1"), (runs_mean(f, model, "eval_fpr@thr1:B_gen_luna") + runs_mean(f, model, "eval_fpr@thr1:B_gen_glm5")) / 2))
mb = J("encoder_v1_dialect_seed0.json")["v1-modernbert-detok"]
rows.insert(2, ("ModernBERT-base, fine-tuned*", runs_mean("encoder_v1.json", "encoder:v1-modernbert-detok", "test_tpr@thr1"), (mb["fpr:B_gen_luna"] + mb["fpr:B_gen_glm5"]) / 2))
pg = J("guards_zeroshot_pg2.json")["pg2-86M"]["flag_rate_native"]
rows.append(("Prompt Guard 2 86M, off the shelf", pg["test_swarm"]["rate"], (pg["B_gen_luna"]["rate"] + pg["B_gen_glm5"]["rate"]) / 2))
ad = J("guards_agentdog.json")["agentdog"]["flag_rate_native"]
abl = J("agentdog_ablation.json")
rows.append(("AgentDoG 1.5 4B, off the shelf (per call)", ad["test_swarm"]["rate"], (abl["gen_luna|neutral_redacted"]["flag_rate"] + abl["gen_glm5|neutral_redacted"]["flag_rate"]) / 2))

fig, ax = plt.subplots(figsize=(6.4, 3.6))
y = range(len(rows))[::-1]
h = 0.36
for (label, tpr, fpr), yy in zip(rows, y):
    ax.barh(yy + h / 2 + 0.02, 100 * tpr, height=h, color=BLUE, zorder=3)
    ax.barh(yy - h / 2 - 0.02, 100 * fpr, height=h, color=ORANGE, zorder=3)
    ax.text(100 * tpr + 1, yy + h / 2 + 0.02, f"{100*tpr:.1f}%", va="center", fontsize=7.5, color=INK)
    ax.text(100 * fpr + 1, yy - h / 2 - 0.02, f"{100*fpr:.1f}%", va="center", fontsize=7.5, color=INK)
ax.set_yticks(list(y))
ax.set_yticklabels([r[0] for r in rows], fontsize=8, color=INK)
ax.set_xlim(0, 112)
ax.grid(axis="x", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ax.set_xlabel("Flagged (%)")
ax.legend(handles=[Patch(color=BLUE, label="Swarm Traces actions (held-out clusters)"), Patch(color=ORANGE, label="Benign agent-style scripts by GPT-6 Luna and GLM 5 (mean)")],
          frameon=False, fontsize=7.5, loc="lower center", bbox_to_anchor=(0.35, 1.0), ncol=1)
fig.text(0.01, 0.005, "Learned monitors: 1%-validation-FPR threshold, mean of 3 seeds (*ModernBERT on the scripts: seed 0).\nOff-the-shelf guards: their own threshold. Sources: results/gate_v1b, encoder_*, guards_*, agentdog_ablation.json", fontsize=6.3, color=INK2)
fig.tight_layout(rect=(0, 0.06, 1, 1))
fig.savefig(f"{OUT}/fig_monitors.png", dpi=220)
fig.savefig(f"{OUT}/fig_monitors.pdf")
json.dump({"fig_monitors_rows": rows}, open(f"{OUT}/figure_data.json", "w"), indent=1)
print("\n".join(f"{l:45s} swarm {100*t:5.1f}%  benign-agent {100*f:5.1f}%" for l, t, f in rows))

# ---------- Figure 3: natural benign tool calls (no style prompt) from six models ----------
nat = pd.DataFrame(J("natural_6models.json")["runs"])
order = [("luna", "GPT-6 Luna"), ("llama4", "Llama 4 Maverick"), ("terra", "GPT-6.1 Terra"), ("mistral", "Pixtral Large"), ("gptoss", "gpt-oss-120b"), ("glm5", "GLM 5")]
fam_color = {"openai": BLUE, "other": ORANGE}
fig, ax = plt.subplots(figsize=(6.0, 3.2))
ax.grid(axis="x", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
for i, (key, label) in enumerate(order[::-1]):
    d = nat[(nat.model == "tfidf:detok") & (nat.set == key)]
    js = nat[(nat.model == "tfidf:detok") & (nat.set == key + "|js")]
    s = nat[(nat.model == "probe:style") & (nat.set == key)]
    fam = d.family.iloc[0]
    ax.barh(i, 100 * d.fpr.mean(), xerr=100 * d.fpr.std(), height=0.55, color=fam_color[fam], error_kw=dict(ecolor=INK2, lw=0.8, capsize=2), zorder=3)
    ax.plot(100 * js.fpr.mean(), i, marker="o", markersize=7, markerfacecolor=SURFACE, markeredgecolor=INK, markeredgewidth=1.5, zorder=4)
    ax.plot(100 * s.fpr.mean(), i, marker="|", markersize=12, color=INK, markeredgewidth=2, zorder=4)
    ax.text(101, i, f"{100*d.fpr.mean():.0f}%  (n={int(d.n.iloc[0])})", va="center", fontsize=7.5, color=INK)
ax.set_yticks(range(len(order)))
ax.set_yticklabels([l for _, l in order[::-1]], fontsize=8, color=INK)
ax.set_xlim(0, 118)
ax.set_xlabel("Benign tool calls flagged (%)")
from matplotlib.lines import Line2D
ax.legend(handles=[Patch(color=BLUE, label="OpenAI model: all calls"), Patch(color=ORANGE, label="Other family: all calls"),
                   Line2D([], [], marker="o", linestyle="", markerfacecolor=SURFACE, markeredgecolor=INK, label="JavaScript calls only"),
                   Line2D([], [], marker="|", linestyle="", color=INK, markersize=10, markeredgewidth=2, label="style-only probe")],
          frameon=False, fontsize=7, loc="lower center", bbox_to_anchor=(0.45, 1.0), ncol=2)
fig.text(0.01, 0.005, "Each model acts as an agent with run-code tools, no style instructions, same 400 tasks. TF-IDF monitor (detok) at its 1%-validation-FPR\nthreshold, mean ± std over 3 seeds. Swarm actions at that threshold: 100% flagged. Source: results/natural_6models.json", fontsize=6.2, color=INK2)
fig.tight_layout(rect=(0, 0.07, 1, 1))
fig.savefig(f"{OUT}/fig_natural.png", dpi=220)
fig.savefig(f"{OUT}/fig_natural.pdf")
