# Audit changes after the v2 freeze

Recorded so the drift that `freeze.py --check` reports for `audit/verify_episode.py` is explained.

- 2026-11-09 10:45 IST. `verify()` defaults `max_turns` 12 -> 20 and `wall_s` 240 -> 360 to match
  the v2 limits (the amendment updated the harness but not these audit defaults).
- 2026-11-09 10:45 IST. Ledger rows are scoped to the episode window (t0 - 120 s to t_end + 120 s)
  before matching. v1 and v2 reuse episode ids, and the harness call_key is
  `episode/actor/turn/attempt`, so v1 rows collided with v2 rows in the shared ledger. The check
  logic is unchanged. Side effect for spend: the harness cap guard counts the last row per
  call_key, so colliding v1 rows (about 0.03 USD) were dropped from its running total. The
  spend report recomputes HF spend from every row by time.
- 2026-11-09 10:50 IST. `score.py` takes the B sender and receiver from `schedule_row.sender` and
  `schedule_row.receiver` when the actor role field is `A`/`B` (the harness stores schedule role
  keys). Before this fix every B episode was flagged "without sender and receiver roles" and
  had no B outcome. Pairing labels in scores are sorted (`deepseek-glm`); the schedule says
  `glm-deepseek`. Same cell.

## Drift readback after the run (`audit/post-freeze-drift.json`, 12:25 IST)

`freeze.py --check` lists every file that differs from the v2 freeze. None of the collection code
changed: `harness/`, `tasks/` (including `schedule.json`), `node/` code and `run_collection.sh` match
the freeze. The drift is: the two audit fixes above (`verify_episode.py`, `score.py`); the labelled
exploratory scripts added during or after collection (`channel_signal.py`, `monitor_export.py`,
`merge_reviews.py`, `validation/replay_b0407.py`, `validation/monitor.py`); `REVIEW-INSTRUCTIONS.md`
(fixed before any review); time-stamped addenda appended to `PROTOCOL.md`; and output files.
- 2026-11-09 12:10 IST. `merge_reviews.py`: packets with one pass were to keep it without being able
  to turn positive. In the end every packet got two passes (the last 5 by GPT-6.1 Sol), so this path
  is unused.
