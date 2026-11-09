# P2b: do gateway changes remove the P2 timing signal?

## Question
P2 found a receiver-visible timing association under separate repositories. Does giving each tenant its own gateway, or adding a fixed 12 ms response floor, remove it?

## Setup
- One fixed sender protocol: for active trials, 128 GETs of its own cached `pkg-00` at concurrency 8 over 2 s; nothing for idle trials. The receiver makes 15 timed GET/HEAD probes of its own `pkg-15`, then 6 post-window probes. Labels are balanced within each sender direction.
- Four arms: shared gateway, per-tenant gateways (each bound to its client), shared gateway with a 12 ms response-header floor, shared gateway physically idle (A/A). Each arm: 8 smoke, 100 calibration, 300 held trials.
- One decoder: the median of the 15 timed probes against the midpoint of the two calibration class medians. All four decoders froze together at 2026-11-09 03:51:33 UTC. Primary family: four one-sided exact binomial tests against 0.5, Holm-corrected.

## Result (from the study report)
| Arm | Correct / 300 | Accuracy | TP / FP | Holm p | Probe median / p95, ms |
| --- | ---: | ---: | --- | ---: | --- |
| Shared gateway | 220 | 73.33% | 70 / 0 | 5.7326e-16 | 4.558495 / 8.116588 |
| Per-tenant gateways | 197 | 65.67% | 50 / 3 | 6.2192e-8 | 6.061501 / 8.541694 |
| Shared, 12 ms floor | 216 | 72.00% | 101 / 35 | 2.2005e-14 | 13.623434 / 14.138319 |
| Shared, idle A/A | 146 | 48.67% | 80 / 84 | 0.698297 | 3.937477 / 4.976668 |
- Neither intervention removes the signal. The floor adds latency and keeps above-chance accuracy, which rejects the prospective floor prediction on this stack. Wilson 95% intervals: 68.06-78.02%, 60.13-70.81%, 66.67-76.78%, 43.06-54.30%.
- Secondary Fisher tests: shared vs per-tenant p = 0.0509298; shared vs floor p = 0.783519. The 7.67 and 1.33 point accuracy differences do not establish a reduction.
- The native held audit passes 552,674 checks. All 25,500 padded requests join to gateway records; headers began at least 12.012926 ms after request start (median 12.073798, p95 12.291415, max 161.979240 ms). Positive sender overruns: 42, 38, 24 trials (none in A/A).
- Edition 3's smoke run failed because the public provision omitted the fixture ETag; edition 4 copied it and used fresh fixtures. The failure stays in `archive/v3`.
- Not shown: a unique Artifactory, storage or host cause; capacity; autonomous communication. Arms ran in sequence on one host. No provider calls.

## Files
- `P2b-attribution/`: `PROTOCOL.md`, `PREDICTIONS.md` (written before data), `DECODERS-FROZEN.json`, `SOURCES-SHA256.txt`, amendments, `P2-GATE.json`, `p2b.py`, `gateway_pad.py`, `sequence.py`, `analyze.py`, `audit.py`, `results.json`, `data/<arm>/` (sealed, provision, schedule, cleanup JSON), `archive/v1` to `v3`.
- `P2b-recovery/`: held-data review (`HELD-REVIEW.json`), calibration review, transport hash records, primary-run record. `P2b-verification/`: independent arithmetic (`verify.py`, `RESULT.json`, `CHECKS.json`, `VERIFICATION-FROZEN.json`).

## How to rerun
Run `../make-evidence-tree.sh`; `python3 -B -m unittest` passes 20 tests in `_evidence/P2b-attribution` and 8 in `_evidence/P2b-verification`. In `P2b-recovery` one test needs the held server logs and errors without them. `analyze.py` and `verify.py` need the held rows (not included). Collection needs the AWS lab (Docker VM with Artifactory).

## Not included
35 files, 308.8 MB of source files were left out. By category: tarballs and archives: 4 files, 19.8 MB; raw data files over 1 MB: 1 file, 1.3 MB; raw data files over 2 MB: 22 files, 287.7 MB; source READMEs (preparation-stage notes): 3 files. The omitted files are per-arm `holdout.jsonl`, `calibration.jsonl`, `server.json` and server log lines, and the public transport tarballs. Raw logs and data are available on request.

## Edited for release
33 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. the lab hostname replaced by `labhost` in PROTOCOL.md, PREDICTIONS.md, p2b.py, audit.py, transport.py and their archive copies, the pre-data readbacks and sequence-v3.log; absolute paths replaced in failure and review records under P2b-recovery, P2b-verification and archive/v3. 22 of these 33 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
