# P9: does a shared object checksum matter?

## Question
Artifactory stores one file per checksum across repositories. Does it matter for the P2 timing signal whether the two tenants' hot objects share a checksum? P9 compares equal-content and distinct-content objects under per-tenant gateways, each with an idle control.

## Setup
- Four cells: `equal`, `distinct`, `aa-equal`, `aa-distinct`. The A/A cells run no sender HTTP under either label.
- Question as frozen in the protocol: does checksum sharing between the sender's hot `pkg-00.bin` and the receiver's hot `pkg-15.bin` contribute to their concurrent HTTP timing association? In `equal`, both actors' object 0 and the receiver's object 15 hold the same 31-byte payload A. In `distinct`, both actors' object 0 hold B, which changes only the first byte of A; object 15 stays A. Objects 1 to 15 stay A in every cell. Same quotas, request grammar and schedules as P2b edition 4.
- The protocol, source freeze, readiness checks and an independent arithmetic verifier were written before any P9 data.
- Edition 1 collected 32 smoke trials (`p9-s1-*`); its new response validator expected a dictionary where the worker returns header pairs, and the audit stopped. It is kept under `archive/science-v1`. `AMENDMENT.md` describes edition 2, declared before any calibration or held trial.
- Edition 2: 32 smoke (`p9-s2-*`), 400 calibration (100 per cell) and 1,200 held trials (300 per cell), 1,632 in all. One decoder per cell was fitted once on that cell's calibration trials and frozen before the first held trial (`DECODERS-FROZEN.json`). Thresholds: `equal` 3.60883225 ms and `distinct` 3.59001575 ms, both predicting activity below the threshold; `aa-equal` 3.8859325 ms (predicts activity above) and `aa-distinct` 3.68963525 ms (below).
- Cells ran one after another on one host, in the order `equal`, `distinct`, `aa-equal`, `aa-distinct`, about 18 minutes each. Primary test: exact conditional assignment tail with labels balanced within each sender direction, four-cell Holm correction at 0.05. Secondary: one two-sided Fisher comparison of `equal` against `distinct`. Zero provider calls and no extra VM.

## Result
| Cell | Correct | Accuracy | Wilson 95% interval | Primary raw p | Holm p |
| --- | ---: | ---: | --- | ---: | ---: |
| equal | 254/300 | 0.84667 | [0.801534, 0.883034] | 6.005e-36 | 1.802e-35 |
| distinct | 267/300 | 0.89000 | [0.849544, 0.920595] | 7.486e-47 | 2.995e-46 |
| aa-equal | 163/300 | 0.54333 | [0.486774, 0.598797] | 0.07964 | 0.1593 |
| aa-distinct | 141/300 | 0.47000 | [0.414259, 0.526500] | 0.8953 | 0.8953 |
- Equal minus distinct accuracy is -4.333 percentage points; Fisher two-sided p = 0.147. That shows neither a reduction nor equivalence.
- Both idle gates pass, raw and Holm-corrected (`null_gate_passed` is true in `results.json` and `RESULT.json`). `aa-equal` raw p of 0.0796 is close to the line.
- Confusion matrices (TP / TN / FP / FN): equal 124 / 130 / 20 / 26; distinct 139 / 128 / 22 / 11; aa-equal 68 / 95 / 55 / 82; aa-distinct 41 / 100 / 50 / 109. All 1,200 held trials are scored; no trial had a failed request marker.
- Native audits pass in every split: 13,744 checks (smoke), 170,512 (calibration), 511,312 (held), 695,568 in all.
- The independent verifier (`P9-verification/RESULT.json`) reproduces every count, confusion matrix, median, p-value, the Holm values and the Fisher p from integer arithmetic. It reuses the original native auditor, so the request checks are not independent.

Plain meaning: giving the two tenants' hot objects different checksums did not remove the timing signal. Distinct content still scores 267/300 against idle controls near chance (163/300 and 141/300).

Boundaries:
- Only object 0 changed. Objects 1 to 15 stay shared, as do Artifactory and the host.
- Realized sender overruns past the planned two-second schedule were 50 of 150 executed trials in `equal` and 32 of 150 in `distinct` (all in label-1 trials; none in the A/A cells). All trials stay in the denominators. The overrun difference is not explained here.
- The idle cells ran in later windows (about 07:03 to 07:39 UTC, after the active cells) and use their own fitted decoders, including an opposite direction for `aa-equal`. They test the fitting procedure on idle data, not the active thresholds at the time of the active cells.
- The result does not show that checksum sharing contributes nothing, and it does not identify a unique cause. Other catalog objects still share content.
- It is not a matched replication of P2b on another host, so its scores are not pooled with P2b.
- One sequential run on one host; Wilson intervals assume independent trials and do not cover serial dependence. A non-significant Fisher test is not an equivalence test. No capacity or mechanism claim follows.

## Files
- `P9-content/`: `PROTOCOL.md`, `AMENDMENT.md`, `SELECTION.json`, `SOURCES-FROZEN.json`, `SOURCES-SHA256.txt`, `PARENT-SOURCES.json`, `DECODERS-FROZEN.json`, `ALLOW-COLLECTION.json`, `READINESS-CHECKS*.json`, `results.json` (primary result), code (`gate.py`, `p2b.py`, `native_storage.py`, `upstream_content.py`, `sequence.py`, `audit.py`, `analyze.py`), `test_readiness.py`, `*-sequence-status.json` and `*-sequence.log` for smoke, calibration and holdout, `gate-evidence/` (copies of the P2 and P2b results the gate checks), `data/` (native audits for all three splits; per cell the seals, schedules, provisioning, cleanup and native before/after records; smoke trial files and server records), `archive/science-v1`.
- `P9-verification/`: independent verifier (`verify.py`, `RESULT.json`, freezes, numerical checks) and tests.
- `P9-review/`: JSON review records: calibration review, held-data review (per-trial sender overruns, receiver schedule lead), pre-primary chronology review, review preparation and a record of the report clarifications that were accepted.
- `P9-readiness/`: JSON records of metadata readbacks, model publication, launch and transport of each split, pre-data refusals, and the runtime readback after the last cell.
- `P9-audit-recovery/`: the six edition 2 header regressions (`test_headers.py` with before and after results).

## How to rerun
Run `../make-evidence-tree.sh`. In `_evidence/P9-verification`, `python3 -B -m unittest` passes 3 of 4 tests; the fourth asserts that `RESULT.json` and `data/` do not exist yet, so it fails now. `test_headers.py` in `P9-audit-recovery` passes 6 of 6 when `P9-audit-recovery` is copied next to `P9-content` (the script does not copy it). `test_readiness.py` needs a native storage result from a lab folder not in this release.

`analyze.py` cannot recompute `results.json` from the files here, and this was tried. It needs `calibration.jsonl` and `holdout.jsonl` for all four cells, which are omitted for size, and the audit replay needs the omitted server records. It also checks `SOURCES-SHA256.txt` first: the release copies of `PROTOCOL.md` and `audit.py` were edited, so it stops at "P2b frozen source changed: PROTOCOL.md". The check can only pass with the original files. The original per-cell files are available on request. The reported numbers can be checked against `P9-verification/RESULT.json`, `data/audit-holdout.json` and the seals, which hold the SHA-256 of each omitted file.

Collection needs the AWS lab with per-tenant gateways on Artifactory.

## Not included
59 files, 212.5 MB of source files were left out. By category: raw trial files over 2 MB (`calibration.jsonl`, `holdout.jsonl`, 4 cells each): 8 files, 123.8 MB; server records over 2 MB (`*-server.json`): 6 files, 35.2 MB; server log lines over 2 MB (`*-server-log-lines.txt`): 6 files, 48.8 MB; calibration server records under 2 MB for the two A/A cells, dropped so no cell has partial calibration records: 4 files, 4.6 MB; source READMEs (preparation-stage notes): 7 files; operational helper code (`launch.py`, `publish.py`, `transport.py`, `models.py`, `test_transport.py`, `review.py`): 6 files, 0.04 MB; the earlier `P9-readiness/archive` records: 19 files, 0.07 MB; review prose and its preservation record: 3 files, 0.02 MB. Raw logs and data are available on request.

## Edited for release
10 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. The lab hostname replaced by `labhost` in PROTOCOL.md and audit.py and in their archive/science-v1 copies; absolute home paths `/Users/<user>` replaced by `~` in 6 readiness, review and recovery JSON records. 6 of these 10 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV. AWS resource IDs in `gate-evidence/`, `gate.py` and `ACTUAL-PUBLICATION.json` are left as they are.
