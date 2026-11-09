# P2: concurrent load against the hardened designs

## Question
P1 probes after the sender stops. Does a receiver still observe the other tenant's activity while load overlaps, under separate repositories with origin binding and under private immutable serving?

## Setup
- Four arms, each 300 calibration and 1,000 fresh held trials: separate Artifactory repositories with origin binding (GET/HEAD only, storage API denied); a sham whose sender loads an isolated dummy service on the same host; A/A (sender never acts); private immutable serving (one serving process per tenant).
- Eight bounded sender recipes: up to 128 requests, 8 concurrent, nominal 2 s window. The receiver runs a fixed during/after-window probe battery. Both actors are dispatched on every trial; the idle control sends no sender HTTP under either label.
- Classifiers frozen after all four calibrations were audited. Primary test: 10,000 label randomizations within recipe-by-direction cells, Holm over 4 arms. All numbers on one shared host, run sequentially. No model calls.

## Result (from the study report)
| Arm | Correct | Accuracy | TPR | FPR | Holm p | 98.75% interval |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Separate repositories | 942/1,000 | 94.2% | 92.2% | 3.8% | 0.000400 | 92.10-95.89% |
| Concurrent dummy service | 657/1,000 | 65.7% | 68.4% | 37.0% | 0.000400 | 61.84-69.42% |
| A/A, always idle | 517/1,000 | 51.7% | 65.0% | 61.6% | 0.148 | 47.70-55.68% |
| Private immutable serving | 562/1,000 | 56.2% | 21.2% | 8.8% | 0.000400 | 52.21-60.13% |
- 0.000400 is the Monte Carlo floor (uncorrected 1/10,001), not zero. A/A rates refer to randomized labels. Exact conditional tails from the independent verifier: about 2.30e-222, 1.03e-23, 0.1474 and 2.64e-8 in table order.
- Separate repositories confirm 120 of 121 selected feature comparisons: the median timed-probe latency is 0.464 ms lower on active trials (corrected p about 1.04e-117), the first timed probe 1.055 ms lower. The sham confirms 1 of 2 (first probe 0.169 ms lower). Immutable serving confirms 0 of 13; its timed-median difference is +0.0147 ms and not significant.
- Receivers get faster, not slower, under the other tenant's load. Calibration-only attribution (`P2-calibration-attribution`): GET client median 3.56 ms active vs 4.04 idle on separate repositories, 4.06 vs 4.02 in the sham, 4.02 vs 4.03 in A/A, 1.25 vs 1.32 for immutable serving; the effect appears during the window and not after it. This is exploratory, not a confirmatory result.
- Native audits pass: 4,000 held trials, 2,236,084 checks, 276,018 client requests (calibration: 1,200 trials, 670,820 checks). Sender requests ran past the nominal window in 150 separate, 64 sham and 208 immutable trials (none in A/A); all stay in the denominators. One A/A trial (`h0302`) had a late receiver setup; it was kept.
- Not shown: a timing cause, a model exchange or channel capacity. JFrog documents checksum-based storage across repositories, which makes a content-hash explanation plausible; it is untested here (P9 was prepared for it).

## Files
- `P2-residual/`: `PROTOCOL.md`, freezes (`FROZEN.json`, `SOURCES-FROZEN.json`, `CONTROL-FROZEN.json`), amendments, `gate-evidence/`, harness/worker/gateway/service/audit/analysis code and tests, `results/p2-m1/` (calibration and holdout results, frozen models), `data/p2-m1/<arm>/` (sealed, provision, schedule, cleanup, audit JSON), `archive/v1`, `archive/v2` (earlier editions).
- `P2-verification/`: independent arithmetic (`verify.py`, `RESULT.json`, `VERIFICATION-FROZEN.json`, `HELD-TIMING-DIAGNOSTIC.json`). `P2-calibration-attribution/`: `decompose.py`, `by_recipe.py`.
- `results/`: held-accuracy figure and generator.

## How to rerun
Run `../make-evidence-tree.sh`; in `_evidence/P2-residual` `python3 -B -m unittest` runs 83 tests and in `_evidence/P2-verification` 2 tests; all pass. `analysis.py`, `audit.py` and `verify.py` need the held rows (not included; about 756 MB in the lab). Collection needs the AWS lab: one 4-vCPU VM with Docker and Artifactory. `verify.py` was run with NumPy/SciPy and one BLAS thread.

## Not included
43 files, 782.1 MB of source files were left out. By category: run-state files: 3 files; tarballs and archives: 3 files, 14.9 MB; raw data files over 2 MB: 22 files, 767.2 MB; run-process notes: 3 files; source READMEs (preparation-stage notes): 1 file. The omitted files are the `calibration.jsonl` and `holdout.jsonl` rows (up to 127 MB each), `server.json` and `server-log-lines.txt` per arm, and three deployment-preparation tarballs. Raw logs and data are available on request.

## Edited for release
9 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. the lab hostname replaced by `labhost` in PROTOCOL.md, COLLECTION-SEQUENCE.md, controller.py, the same files under archive/v1 and v2, and publish_gate.py; absolute paths replaced in VERIFICATION-FROZEN.json and COLLECTION-SEQUENCE.md. 8 of these 9 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
