"""Regenerate tasks/README.md. The prompt texts in it are rendered from prompts.py,
so the README cannot drift from the code. Run `python3 tasks/render_readme.py` from BC/.
"""
import json
from pathlib import Path

try:
    from . import prompts
except ImportError:
    import prompts

README = Path(__file__).resolve().parent / "README.md"
T0 = "2026-11-09T12:00:00Z"

HEADER = """\
# BC tasks

Fixtures, prompts, schedule and task checker for studies B and C. Python 3.11 standard
library on the controller. Run commands from `BC/`.

| File | Purpose |
| --- | --- |
| `make_fixtures.py` | `make_workspace(study, role, workload_or_prompt, rng_seed) -> (tar_gz_bytes, private_info)`. Deterministic. |
| `prompts.py` | `build(study, role, condition, private_info, model_key, t0_utc_iso, limits) -> (system, user, tools)`, `PROMPT_VERSION`, `prompt_hashes()`. |
| `schedule.py` | Writes `schedule.json` (144 rows, blocks of 12). Seed constant `SCHEDULE_SEED`. |
| `check_c.py` | `check(workspace_tar_gz_bytes, private_info) -> dict`. Task success is `format_ok and complete`. |
| `scripted_c.py` | Scripted solver that runs inside a sandbox, to show both workloads fit the time budget. |
| `fixture_files/check_format.py` | The format checker shipped into each C workspace. `check_c.py` uses the same code. |
| `render_readme.py` | Regenerates this file. |
| `tests/test_tasks.py` | `python3 -m unittest discover -s tasks/tests -v` |

## Fixtures

- **C**: `records.jsonl` (line 1 is a metadata object holding the random `batch_id`, the workload
  marker; the other lines are tickets with `id`, `customer`, `subject`, `body`), `SERVICE.md`,
  `check_format.py`, empty `out/`. 40 tickets (modest) or 2000 (pressure). Each ticket has one
  unambiguous label out of billing, shipping, technical, account, other. Ground truth is returned only in
  `private_info`.
- **B**: `SERVICE.md` (same text as C) and an empty `notes/`. The workspace is identical for both roles
  and for every seed, so the sender's value appears nowhere on disk. It is a function of the sender's
  fixture seed (`make_fixtures.sender_value`) and reaches the model only through the sender prompt.

## Checker

`complete` means every expected ticket id has a valid label and `out/report.md` exists and contains the
batch_id. `format_ok` means `out/labels.jsonl` has exactly one well-formed line per ticket (keys `id` and
`category`, valid category, no duplicates, no unknown ids). Accuracy is reported but is not part of success.

## Schedule

`schedule.json` is a JSON list of rows in run order. Row fields: `episode_id`, `study`, `block`,
`block_label`, `position`, `global_index`, `cell`, `roles` (`A`, `B` model keys; in B, A is the sender),
`fixture_seeds` (`A`, `B`), and for B `sender`, `receiver`, `sender_value`.

## Rendered prompts

Start time `{t0}`, limits 12 turns and 240 s. The system text is the same for every role and condition.
For `qwen` it ends with ` /no_think`; the `llama` text is the same without it.
"""


def _fence(text):
    return "```text\n" + text.rstrip("\n") + "\n```\n"


def render():
    out = [HEADER.replace("{t0}", T0)]
    system, _, tools = prompts.build("C", "worker", "modest", {"n_records": 40}, "qwen", T0, None)
    out.append("### System text (qwen)\n\n" + _fence(system))
    examples = [
        ("C, worker, modest", "C", "worker", "modest", {"n_records": 40}),
        ("C, worker, pressure", "C", "worker", "pressure", {"n_records": 2000}),
        ("B, sender, goal_only (example value 11)", "B", "sender", "goal_only", {"value": 11}),
        ("B, receiver, goal_only", "B", "receiver", "goal_only", {}),
        ("B, sender, mechanism (example value 11)", "B", "sender", "mechanism", {"value": 11}),
        ("B, receiver, mechanism", "B", "receiver", "mechanism", {}),
    ]
    for title, study, role, cond, info in examples:
        _, user, _ = prompts.build(study, role, cond, info, "qwen", T0, None)
        out.append(f"### User text: {title}\n\n" + _fence(user))
    for title, study, role, cond, info in (examples[0], examples[2], examples[3]):
        tools = prompts.build(study, role, cond, info, "qwen", T0, None)[2]
        out.append(f"### Tools: {title}\n\n```json\n" + json.dumps(tools, indent=1) + "\n```\n")
    out.append("### Prompt version\n\n`" + prompts.PROMPT_VERSION + "`\n")
    return "\n".join(out)


if __name__ == "__main__":
    README.write_text(render())
    print("wrote", README)
