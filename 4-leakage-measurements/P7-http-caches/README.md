# P7: NGINX and Squid 404 caches

## Question
Does the shared negative-cache state seen in Artifactory also appear in two common HTTP caches, and which configuration removes it?

## Setup
- NGINX 1.30.6 (404 cached 60 s) and Squid 7.8 (nondefault `--enable-http-violations` build, `negative_ttl` 60 s), each with an origin delay of 20 ms or 150 ms. Four arms per cache: shared, trusted tenant key (derived from the client's network address), private cache instances, cache disabled; plus randomized-label no-sender arms.
- Sender fetches a fresh missing object before the receiver starts. A timing decoder uses only receiver elapsed time, fitted on calibration, frozen, then applied to 32 holdout trials per cache arm and 16 per no-sender arm (48 calibration and 576 held trials in total). Exact label randomization within direction, Holm over 4.
- Native cache and origin logs separately check the cause (whether the receiver's origin lookup was skipped).
- Cost: no model calls; a $3.20 VM-time reservation.

## Result (from the study report)
| Cache, delay | Shared | Trusted key | Private | Disabled | Shared Holm p |
| --- | ---: | ---: | ---: | ---: | ---: |
| NGINX, 20 ms | 31/32 | 16/32 | 16/32 | 16/32 | 1.087e-7 |
| NGINX, 150 ms | 32/32 | 16/32 | 16/32 | 16/32 | 2.415e-8 |
| Squid, 20 ms | 29/32 | 16/32 | 16/32 | 16/32 | 9.962e-7 |
| Squid, 150 ms | 32/32 | 16/32 | 16/32 | 16/32 | 2.415e-8 |
- Native cause, totals over the four cache/delay settings: 64 completed sender fills per route. In the shared cache the receiver then made 0 origin lookups (0/64); with trusted keys, private caches and caching disabled it made one in all 64 (64/64). Idle shared trials kept their lookup.
- No-sender arms scored 8/16 each. Every defended decoder always predicted idle. That describes these decoders only.
- Utility: all 60 ordinary GET canaries returned the exact 40-byte object. Cost: origin requests per cohort were 34 (NGINX) and 39 (Squid) shared, 50 and 55 keyed or private, 56 disabled. Total audited client-plus-origin work: 2,058 requests.
- This measures a known mechanism under explicit configuration. It is not a new channel, and all routes still share the VM and kernel.
- Failures kept: edition g1 failed before its first request (read-only NGINX temp prefix); the g2 calibration audit first failed on Squid setup connections and was amended before decoder fitting.

## Files
- `PROTOCOL.md`, `FROZEN.json`, `SOURCES-FROZEN.json`, `AMENDMENT-v2/v3.json`, `deployment-preparation-manifest.json`.
- Code: `build.py`, `Dockerfile`, `collect.py`, `service.py`, `origin.py`, `store_id.py`, `request_guard.py`, `plan.py`, `configuration.py`, `analysis.py`, `audit.py`, tests.
- `models/g2/` frozen decoders; `build/g2/build-result.json` binary hashes; `data/g2-cal`, `data/g2-main` (per-cohort trials, sealed, server records, `holdout-results.json`, `audit-results.json`); `data/g1-cal` (failed first edition); `sources/provenance.json` (source URLs and hashes); `archive/v1`, `v2`.
- `results/`: standalone figure, descriptive counts, generator.

## How to rerun
Run `../make-evidence-tree.sh`, then `python3 -B -m unittest` in `_evidence/P7-caches` (24 tests, all pass). `audit.py data/g2-main` and `analysis.py holdout` can be tried on the shipped cohort files; the source tarballs for NGINX and Squid are not included (URLs and SHA-256 are in `sources/provenance.json`). Collection needs a Docker host that can build both caches (the lab used 0.5 CPU and 2 GiB for the build); I did not rerun it.

## Not included
24 files, 39.0 MB of source files were left out. By category: source READMEs (preparation-stage notes): 3 files; tarballs and archives: 9 files, 39.0 MB. The archives are the NGINX and Squid release tarballs and the deployment-preparation bundles. Audit-amendment records and public transport receipts for P7 live in other lab folders not in this release. Raw logs and data are available on request.

## Edited for release
9 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. the lab hostname replaced by `labhost` in Dockerfile, PROTOCOL.md and build.py (current and archived editions) and two archived READMEs. 9 of these 9 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
