# P1: generic leakage meter

## Question
Can a generic differential test find shared-state channels in a read-only service without being told what to look for, and does it stay quiet on hardened designs and null controls?

## Setup
- Per trial the sender runs one of 12 actions (GET, HEAD, conditional GET, 1-byte range GET, statistics or properties reads, two folder listings, GET or HEAD of a rendezvous missing path, GET of a fresh path, a burst of concurrent downloads) or stays idle for 1 s. The receiver then runs a fixed 58-probe battery and records status, headers, body hash, JSON fields and latency. Features are generic; the analysis never sees the sender's action.
- Five arms, each 300 calibration and 1,000 sealed holdout trials: open shared service, combined control (metadata denied, negative caching off), separate repositories, A/A (sender never acts), sham (sender acts on a dummy server).
- Classifiers and feature-confirmation families were frozen before holdout retrieval (`P1-meter/FROZEN.json`, 2026-11-08 20:24:32 UTC). Holm correction over 5 classifier tests and over 70 selected feature tests.

## Result (from the main-results report and paper Section 4.5)
| Arm | Correct / 1,000 | Accuracy | Experiment-wide p | 99% binomial interval |
| --- | ---: | ---: | ---: | --- |
| Shared open service | 590 | 59.0% | 0.000500 | 54.91% to 63.01% |
| Metadata denied, negative caching off | 513 | 51.3% | 0.6743 | 47.18% to 55.41% |
| Separate repositories | 517 | 51.7% | 0.6143 | 47.58% to 55.80% |
| A/A, physically idle | 490 | 49.0% | 1.0000 | 44.89% to 53.12% |
| Settled dummy sender | 500 | 50.0% | 1.0000 | 45.89% to 54.11% |
- Known mechanisms recovered blind: all 90 open-arm GET trials agree with the download-statistics mechanism (39 active, 51 idle); all 94 rendezvous-GET trials agree with the negative-cache mechanism (47 and 47). The rendezvous latency feature confirms with p = 4.800e-28, effect about -151.97 ms.
- Residual: the combined control has one confirmed timing feature, `fam.head|elapsed_ms|max` in the `load` recipe, p = 0.004580654660534308, 29 active trials against 500 idle, effect about -0.1095 ms. This is not a transmission protocol. P2 follows it up.
- The original frozen causal audit failed because the log collector missed rotated archives. Native recovery found 7,036 omitted rows (4,063 A/A, 2,319 separate, 654 sham). An amended audit, frozen after the holdout was opened, passes all 6,500 trials, 395,959 forwarded requests, 127,709 gateway denials and 30,375 dummy requests (4,893,044 checks). No trial, label, fit or statistic changed. This is a post-holdout amendment.
- The intervals assume independent trials on a shared host. A chance-level arm is a statement about this classifier, not proof of no channel.

## Files
- `P1-meter/`: gateway, service, harness, meter, controller, `analysis.py`, `audit.py`, `report_metrics.py`; `PROTOCOL.md`, `REPORTING-RULES.md`; freezes (`FROZEN.json`, `ANALYSIS-CODE-FROZEN.json`, `REPORTING-FROZEN.json`); `results/analysis-v2/` (calibration and holdout results, `report-metrics.json`, `known-channel-validation.json`, frozen `*.model.pkl`); `data/main-m1/<arm>/sealed.json` (SHA-256 of the sealed holdout), `provision.json`, `cleanup.json`; `data/main-m1/audit-results.json` (the original failed audit).
- `P1-validation/`, `-v2/`, `-v3/`: known-mechanism validation, three editions. `P1-audit-recovery/`: amended audit, `AUDIT-AMENDMENT-FROZEN.json`, recovery inputs and result, diagnosis.
- `results/`: figures (`P1-holdout.*`, `p1-recipes.*`) and their generators.

## How to rerun
Run `../make-evidence-tree.sh`, then in `_evidence/P1-meter`, `P1-validation*` and `P1-audit-recovery` run `python3 -B -m unittest` (24, 7, 8, 8 and 8 tests; all pass in the rebuilt tree). `analysis.py calibrate|freeze|holdout DATA OUT` and the audits need the calibration and holdout rows, which are not included. Collection needs the AWS lab: one Docker VM with Artifactory OSS. The `.pkl` files are pickled models; load them only if you trust this copy.

## Not included
78 files, 1450.9 MB of source files were left out. By category: run-process notes: 2 files; run-state files: 4 files; raw data files over 2 MB: 25 files, 1153.1 MB; source READMEs (preparation-stage notes): 3 files; recovered raw logs and data (code-only folder): 8 files, 297.8 MB; symlinks to files held in another study: 30 files. The omitted holdout files are 74 to 100 MB each; their hashes are in the `sealed.json` files. Raw logs and data are available on request.

## Edited for release
8 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. the lab hostname replaced by `labhost` in PROTOCOL.md, analysis.py, controller.py, meter.py, run.py, one archived analysis copy and retrieval-check.json; absolute paths replaced in ANALYSIS-ENVIRONMENT.json. 4 of these 8 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
