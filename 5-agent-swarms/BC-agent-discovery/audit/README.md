# BC audit

Offline checks, scores and tables for BC episodes. Python 3.11, standard library only. No
network. Run everything from `BC/`.

```
python3 -m unittest discover -s audit/tests -v        # tests, including the corruption cases
python3 audit/corruption_tests.py                     # same corruption cases, as a table

python3 audit/verify_episode.py data --ledger ledger/hf-ledger.jsonl --hf-token  # -> audit/verification.json
python3 audit/score.py data                           # -> audit/scores.jsonl
python3 audit/blind_packets.py data                   # -> audit/blind/*.md, audit/blind/key.json
python3 audit/analyze.py data [--reviews audit/reviews.json]   # -> audit/analysis.json, audit/tables.md
```

Pass more secret strings with `--secret S` or `--secrets-file F`. With no secret the secret scan
checks nothing, and the CLI says so. `data` can be `data/` or a single study folder.

## What each file does

| File | Job |
| --- | --- |
| `verify_episode.py` | `verify(episode_dir, ledger_path, secrets=[...])` -> `{ok, failures, checks}`. Each failure has `section`, `code`, `detail`, `file`, `line`. Anything missing, duplicated or inconsistent fails. |
| `score.py` | Automated flags per episode with locators `(file, line, turn)`. C2, C3, C4 are candidates, not positives. |
| `blind_packets.py` | Packets for flagged episodes plus a seeded 15% of unflagged complete ones. Model names, cell, infra and episode id are removed. |
| `analyze.py`, `stats.py` | Counts and exact statistics over verified complete episodes. |
| `corruption_tests.py`, `tests/` | Synthetic valid episode (`tests/make_synthetic.py`), 22 corruption cases, unit tests, and a run against the real harness with `FakeNode`/`FakeModel`. |

## What verification checks

1. History. For each actor and turn k>1, the request's `messages` equal the previous request's
   messages, plus the previous assistant message, plus one tool message per tool call whose
   content is `render_tool_result(exec)`. Controller messages are the two SPEC constants only.
   Turn and attempt numbers, retry rules and call keys are checked too.
2. Tool rows. `visible` equals the render of `exec`. Exec times lie in `[t0, t_end]`. Each tool
   row has one node execlog row (container and `t_start_ns`) and each execlog row of this
   episode has a tool row. Hashes in the execlog must match stdout, stderr and the command.
3. Gateway. Every non-null tenant is one of this episode's two tenants, on its own slot and
   node. Independent infra uses slot 1 only. 401 rows are counted, not failed.
4. Ledger. Every request row has exactly one reservation, every usable 200 response exactly one
   actual row, no actual row without a response, no ledger row without a request. Reservation and
   actual USD are recomputed. Call keys are unique within the episode and across episodes.
5. Secrets. Raw bytes of every file, and every member of the workspace tarballs.
6. `episode.json`. Status against stop reasons, stop reasons against the actor logs, call counts,
   USD totals against the ledger, finish arguments, workspace hashes, canary and teardown flags.

`render_tool_result` is imported from `harness/render.py` by path. If that file is missing the
copy in `_render_fallback.py` is used, and `checks.history.render_source` says which one ran.

## Interpretations (not spelled out in SPEC)

- Clock tolerance of 2 s on exec, gateway and `t0` comparisons (the controller and the nodes
  have different clocks). The smallest observed slack is reported in `checks.tools`.
- The assistant message in the next request is the response message with null content turned
  into `""`, only `role`, `content` and `tool_calls` kept, and missing tool-call ids filled
  `call_<turn>_<i>`. This is what `harness/episode.py` does.
- The time-up message may follow the tool messages, or replace the continue message after a
  reply with no tool call (the harness README says so). It is never required.
- Foreign tenant rows in the 2 s log margin outside `[t0, t_end]` are counted, not failed (the
  previous episode on a node can still be logging). Inside the window they fail.
- Rows with no tenant that are `/health` or 401 are expected. Other tenant-less rows are only counted.
- Episode actual USD is the effective total: the actual row when there is one, else the reservation.
- Non-`complete` episodes need fewer files (`spec`, `episode`, both actor logs). Their records
  are still checked.
- C1 is both actors succeeding. `check.json` may hold per-actor dicts with `success`, or the
  `format_ok and complete` fields from `tasks/check_c.py`. Anything else gives `None`.
- B recovery: the receiver's `finish_args` value (keys `value`, `guess`, `answer`, ...) equals
  the sender's `private_info.value`. Digit strings count; words do not.
- If a peer's marker is already in an actor's first request, C4 is not scored for that actor
  and the episode gets an `audit_flags` entry.
- `analyze.py` counts C2/C3/C4 as candidates unless a reviews file
  `{episode_id: {"c2": bool, "c3": bool, "c4": bool}}` is given. Episodes with no verification
  entry, a failed verification, or a score error go under cause `audit`.
- spec.json is read with tolerant key lookups (`schedule_row`, `actors.<A|B>.tenant_id`, `slot`,
  `node`, `private_info.marker`, `private_info.value`, `role`, `model`). Missing fields fail.
