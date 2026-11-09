# P9: does a shared object checksum matter? (prepared, no result)

## Question
Artifactory stores one file per checksum across repositories. Does it matter for the P2 timing signal whether the two tenants' hot objects share a checksum? P9 compares equal-content and distinct-content objects under per-tenant gateways, each with an idle control.

## Setup
- Four configurations: `equal`, `distinct`, `aa-equal`, `aa-distinct` (A/A controls have no label-dependent sender HTTP).
- Question as frozen in the protocol: does checksum sharing between the sender's hot `pkg-00.bin` and the receiver's hot `pkg-15.bin` contribute to their concurrent HTTP timing association? `equal`: both tenants read object A. `distinct`: sender B, receiver A. Same quotas, request grammar and schedules as P2b edition 4.
- The protocol, source freeze, readiness checks and an independent arithmetic verifier were written before any P9 data.
- Edition 1 collected 32 smoke trials (8 per cell, `p9-s1-*`); its new response validator expected a dictionary where the worker returns header pairs, and the audit stopped. It is kept under `archive/science-v1`. `AMENDMENT.md` describes edition 2, declared before any calibration or held trial.

## Result
None. There is no calibration or holdout data in this folder and the paper reports no P9 result. The fresh edition 2 smoke (`p9-s2-*`, 8 trials per cell) passes the native audit in all four cells (`data/audit-smoke.json`: 5,484 checks for `equal` and `distinct`, 1,388 for each A/A cell). That checks the harness only. See paper Section 4.6 for the P2 signal that motivated it.

## Files
- `P9-content/`: `PROTOCOL.md`, `AMENDMENT.md`, `SELECTION.json`, `SOURCES-FROZEN.json`, `SOURCES-SHA256.txt`, `PARENT-SOURCES.json`, `READINESS-CHECKS*.json`, code (`gate.py`, `p2b.py`, `native_storage.py`, `upstream_content.py`, `sequence.py`, `audit.py`, `analyze.py`), `test_readiness.py`, `gate-evidence/` (copies of the P2 and P2b results the gate checks), `data/` (smoke only), `archive/science-v1`.
- `P9-verification/`: independent verifier (`verify.py`, freezes, numerical checks) and tests.

## How to rerun
Run `../make-evidence-tree.sh`; `python3 -B -m unittest` in `_evidence/P9-verification` passes 3 of 4 tests (the fourth expects no `data/` directory, which now exists). `test_readiness.py` needs a native storage result from a lab folder not in this release. Collection needs the AWS lab with per-tenant gateways on Artifactory; it was not run for this study.

## Not included
5 files, 0.0 MB of source files were left out. By category: source READMEs (preparation-stage notes): 5 files.  Raw logs and data are available on request.

## Edited for release
4 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. the lab hostname replaced by `labhost` in PROTOCOL.md and audit.py and in their archive/science-v1 copies. 4 of these 4 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
