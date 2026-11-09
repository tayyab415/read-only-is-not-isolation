# System-1 per-call classifier

## Question

If containment fails, a monitor on every tool call is the next defense. Can a cheap classifier, one forward pass per call and no reasoning, trained on the swarm's own attack traffic, catch the swarm and stay quiet on ordinary agent work?

## Setup

- Attack side: 43,784 of the 91,037 Swarm Traces payload rows (at least 40 characters of agent-written text after redaction, a code marker and at least 12 structural tokens), in 17,618 near-duplicate clusters. Labels mean "issued by the swarm", not "harmful when read alone". No Swarm Traces rows are redistributed here.
- Benign training data is public coding-agent commands, JavaScript functions, tool calls and package snippets. Held-out benign sets include scripts in the swarm's console style written by GPT-6 Luna, GLM 5, Llama 4 Maverick and Pixtral Large, and natural tool calls from six models (those plus GPT-6.1 Terra and gpt-oss-120b) on the same 400 tasks with no style instructions.
- Four monitors: TF-IDF with logistic regression, ModernBERT-base, and Llama Prompt Guard 2 at 22M and 86M, fine-tuned. Splits are disjoint by cluster, 70/10/20, with 3 seeds. The threshold is the 99th percentile of validation benign scores (1% validation false-positive rate), applied unchanged to every set. Two published guards, Prompt Guard 2 and AgentDoG 1.5 (4B), run off the shelf.

## Result

- In distribution every trained monitor scores AUC 1.000 (test false-positive rate 0.6 to 1.3%) and still catches 98.8 to 100% of a tactic removed from training.
- The same monitors flag 89 to 100% of benign agent-style scripts from other model families (TF-IDF: Luna 99.7%, GLM 5 99.5%) and 22 to 86% of natural tool calls. TF-IDF on natural calls: Luna 85.7%, Llama 4 78.5%, Terra 75.5%, Pixtral Large 54.0%, gpt-oss-120b 51.4%, GLM 5 22.1%. The swarm's held-out actions are 100% flagged at that threshold.
- On outside attack datasets, paired AUC is 0.49 to 0.70, close to a probe that sees only length and character ratios (0.61 to 0.65). The monitors learned what agent-executed one-shot code looks like, not harm. No swarm action has a detok-cosine twin of 0.8 or more among the 27,432 benign rows searched (structural near twins: 0.06%).
- Adding benign agent scripts from other families lowers false positives on an unseen family from 99.6% (none) to 40.5%, 18.6% and 11.9% (one, two, three families), with swarm detection at 99.8%.
- Off the shelf, Prompt Guard 2 flags 1.5% (86M) and 0.1% (22M) of the swarm's actions. AgentDoG 1.5 flags 99.4% of them per call and 56 to 90% of benign actions. Given each script's real task, its benign false positives fall from about 95% to 12 to 25%.
- Cost per call on a shared 4-thread CPU (upper bounds): TF-IDF 1.2 ms median, Prompt Guard 2 22M 105 ms, ModernBERT-base 227 ms, Prompt Guard 2 86M 305 ms. AgentDoG on an L4 is about $136 per million calls, against about $1 to $2 for the small classifiers.

Limits: labels mean swarm membership, benign agent code from other models is synthetic from one prompt and one task pool, outside sets are small or template-made (27 attacks in the smallest), and held-out false-positive rates move by up to 21 points across seeds and family subsets. All numbers are means over 3 seeds. Prompt Guard 2 targets prompt injection and AgentDoG targets full trajectories, so per-call use understates both.

## Files

| Folder | What it is |
| --- | --- |
| `scripts/` | Pipeline in numbered order, 01 inventory through 24 natural-call evaluation, plus `canon.py` (text views), `cluster.py`, `train_encoder.py`, `score_encoder.py` and the two guard scorers. Some JSON files here are configs; the rest are output files that differ in layout from their namesakes in `results/`, which hold the numbers quoted above. |
| `results/` | One JSON per claim (`gate_v1.json`, `encoder_*.json`, `loto_v1.json`, `natural_6models.json`, `family_curve.json`, `guards_*.json`, `agentdog_ablation.json`, `floor_v*.json`, `latency_*.json`) plus run logs. |
| `figures/` | `fig_monitors`, `fig_natural`, `fig_family_curve` as PDF and PNG, and `figure_data.json`. |

## How to rerun

Offline: `python3 scripts/22_figures.py results OUT_DIR` redraws the figures from the saved results (needs pandas and matplotlib). This works in a scratch directory.

Everything else needs inputs that are not in this repository. Scripts take data paths as arguments and expect the Swarm Traces release (https://swarmtraces.org/data/final/redacted.jsonl.gz), the benign corpora fetched by `10_fetch_hf.py` and `08_doc_snippets.py`, and prepared parquet splits. TF-IDF steps run on CPU. Fine-tuning (`train_encoder.py`) and AgentDoG scoring ran as paid Hugging Face Jobs on an A10G. Benign script generation (`17`, `23`) calls Amazon Bedrock and costs money. Several scripts name private Hugging Face repos for data, encoders and results; those repos are not public.

No trained model, training data or Swarm Traces row is released.

## Not included

- `data_gen/`: 30 files, 8.6 MB of raw generated benign scripts, source lists and generation logs. Raw logs available on request.
- Training and evaluation data and model weights. These were never in the lab folder.
- `PLAN.md` (16.6 KB), `REPORT.md` (19.5 KB), `PAPER_SECTION.md` (5.7 KB), `ARTIFACTORY_INSERT.md` (3.2 KB), `data_cards/` (1 file, 40 KB), `__pycache__` (2 files).
- Source: 122 files, 10.4 MB. Copied: 85 files, 1.7 MB.

## Edited for release

- `results/floor_v1.json` and `results/floor_v2.json`: the `twin_examples` key (18 example rows containing Swarm Traces and benign text) was removed and replaced with a `twin_examples_removed_for_release` note. All numbers are unchanged.
- `results/cpu_jobs.log` and `results/promptguard.log`: a local home-directory path in tracebacks and warnings is now `$HOME`.
