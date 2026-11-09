# Swarm Traces analyses (`text-screens/`, `request-monitor-audit/`)

## Question

`text-screens/`: what can the public Swarm Traces release support for studying polling, delays and coordination, and which text screens find shared-channel code in it? `request-monitor-audit/`: do the earlier scripted-client request detectors (study C) measure what their scores say, and does the release schema match what the screens assume?

## Setup

- `text-screens/` is a read-only analysis of the release's 189,579 rows. It audits the schema, counts rows that match case-insensitive text rules (poll and sleep words, timer calls, read and write syntax, board and reply words), and records which rows a human inspected. Each count is a text candidate, not a tool call, message, execution or agent.
- `request-monitor-audit/` recomputes the same schema counts and re-runs study C's unmodified detector offline on its earlier 1,010-request snapshot (`ds-mid`, 805 s, 880 client-ledger records). It reads the detector code to see which inputs use labels. The snapshot was later replaced in study C, so the audit's numbers describe the earlier one.
- No payload was executed, no URL fetched, no model called.

## Result

The release has 91,037 payload, 23,008 response and 75,534 recovered-text rows, each with exactly seven fields. Every `time_utc` is null, and there is no author, recipient, tool-call ID or request time. The 61,125 parent links all have depth one and mean derivation, not reply. Because of this, the release cannot support a tool-call latency, wait-time or early-detection measurement. A `sleep(8000)` in text is a requested delay.

Row counts by text rule (of 189,579): poll words 118, sleep call syntax 1,043, wait words 922, `setTimeout` or `setInterval` 1,614, board words 32, read syntax 33,473, write syntax 49,164, reply words 21, recipient or reply-to terms 0. There are 163,851 distinct texts, with 19,035 duplicate groups holding 44,763 rows. Of 21 reply-word rows, none was a verified peer-agent reply. The paper (Section 7) states that no row used download statistics or miss timing, and calls this a null on a reconstructed dataset.

Audit of study C's detector on `ds-mid`: recall 1.0 for cache `MKCOL` means 354 of 354 board-labelled writes that returned 201; six others returned 409. The all-directory rule flagged 435 listing rows, 23 of them benign, all from 27 scripted update checks. The detector's D4 stages pick candidates from labelled message types and score against the labelled recipient, so those scores are conditional on labels. Six of 11 inbox writes returned 409. Classifying each request from prior events only changes 2 classifications. A later listing of the parent directory does not show the entry was read: all 13 reply probes lack a later listing of that exact path. These scores are not detection performance on autonomous agents.

## Files

| File | What it is |
| --- | --- |
| `text-screens/analyze.py` | Schema audit, text rules, duplicate and lineage counts. |
| `text-screens/metrics.json`, `run-summary.json` | Counts and denominators. |
| `text-screens/inspection-queue.json`, `review-summary.json` | Row IDs chosen for inspection (eight evenly spaced per rule) and label counts. |
| `text-screens/duplicate-examples.json` | Row IDs of the 20 largest duplicate groups. |
| `text-screens/validate.py`, `validation.json` | Citation and excerpt check: 16 citations, 26 saved rows, 110 excerpts, 189,579 records compared, no problems. |
| `request-monitor-audit/audit.py`, `audit.json`, `parent-recheck.json` | Schema counts and study C audit. |
| `request-monitor-audit/c-ds-mid-output.json`, `c-ds-mid-stdout.txt` | Study C detector output on `ds-mid`. |

## How to rerun

Nothing here reruns from this folder alone. `analyze.py` and `validate.py` expect a workspace root with `library/swarm-data.sqlite` and the downloaded release (`library/sources/.../dataset.jsonl.gz`), and `validate.py` also needs the row records and review files listed under Not included. `audit.py` runs from the workspace root and needs the same SQLite file plus study C's `analysis/` code and `data/ds-mid/`, numpy and pandas. No network, AWS or model calls. The release is at https://swarmtraces.org/data/final/redacted.jsonl.gz (SHA-256 `7b66ab21674de52fcd3f557652f68b1801170c998e2f266862124e6edf283488`, 15,214,685 bytes).

## Not included

- `text-screens/`: 59 of 67 source files (6.7 MB total). `rows/` (52 selected release records, 344 KB), `candidates.tsv` (6.3 MB, one line per candidate row), `manual-review.json`, `reply-review.json`, `timestamp-example.json` and `review.py`, which hold release text, plus the original `README.md` and `review-run-summary.json` (copy of `review-summary.json`).
- `request-monitor-audit/`: nothing (5 of 5 files, 48 KB).
- No Swarm Traces rows or excerpts are redistributed.

## Edited for release

None.
