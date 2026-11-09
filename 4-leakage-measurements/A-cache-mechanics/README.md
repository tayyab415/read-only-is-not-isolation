# A: cache mechanics on Artifactory OSS

## Question
What can ordinary users do to a shared Artifactory remote cache, and which permission allows each action? The tests cover the behaviours described in METR's incident report: noticing another agent's fetched paths, folder-name messages, listing order, files encoded in folder names, nested inboxes, item properties, fabricated page names and deleting messages.

## Setup
- Artifactory OSS 7.161.26 with PostgreSQL, in Docker on one VM, reachable on loopback only. Java heap capped at 1,536 MiB, container limit 4 GiB.
- Identities: anonymous, `a-read` (Read), `a-cache` and `a-cache2` (Read + Deploy/Cache), admin. Grants were changed per control (Deploy-only, Annotate-only, Delete-only, path exclusion).
- Scripted HTTP clients, no language models. 3,375 recorded requests and 71 server log files; every request matches a router or request-log line. Run on 2026-11-07 UTC (2026-11-08 IST).

## Result (from the study report)
- A Read + Deploy/Cache user could fetch a new upstream artifact and create message directories in the shared remote cache. Other readers saw both. Deploy/Cache alone was enough to create directories, including under another user's folder (HTTP 201). Annotate alone could change a known item's properties. Delete was needed to remove someone else's folder.
- Reproduced: shared-cache visibility, directory messages, nested inboxes, properties, encoded files (a second user rebuilt a benign file from four child names with the same SHA-256).
- Not reproduced: fabricated GitHub discussion names (404, nothing retained) and reverse listing order (server listings sorted ascending, so `zz` came last).
- Controls: Read-only returned 403 for an uncached download and for `MKCOL`. `storeArtifactsLocally=false` stopped cache `MKCOL` (501). A permission-target exclude `zz_A_denied/**` returned 403. `offline=true` did not stop `MKCOL` (201). Withholding Annotate stopped property writes.
- Scale: 3,000 child folders were created in 10.06 s (all 201) and every listing returned all 3,000. Names persist up to 255 characters; longer names return 201 but nothing is kept.
- Logging: successful `MKCOL` does not appear in `artifactory-access.log`. Folder `created_by` is NULL for `MKCOL` folders. The request log drops property-write values.
- Limits: one version and configuration, and not OpenAI's deployment. Millions of entries and negative-cache expiry were not tested.

## Files
- `scripts/`: installation, provisioning, test, bulk, collection and audit scripts.
- `bodies/<request-id>.txt` (response body) and `log-matches/<request-id>.json` (matching server log lines) for the non-bulk requests.
- `logs/`: sanitized snapshots of the server logs; `log-inventory.json`, `log-match-index.json`.
- `database/`: read-only SQL commands and results (name length, creator fields, missing paths).
- `bulk-summary.json`, `chunk-roundtrip.json`, `response-byte-audit.json`, `secret-audit.json`, `SHA256SUMS.json`, `image.txt`, `container-hostconfig.json`, `run-logs/`.
- `EVIDENCE-GUIDE.md`: field-by-field description of the evidence.

## How to rerun
Needs a Docker host with about 5 GiB free for Artifactory plus PostgreSQL and outbound access to github.com for the upstream fetches. No GPU, no model calls. `scripts/start.sh`, `database.py` and `provision.py` build the instance, `mechanics.py`, `supplement.py` and `final_tests.py` run the tests, `bulk.py` makes the 3,000 folders, `collect.py` and `audit.py` gather and check the logs. Do not rerun the installation scripts against a running instance. These scripts were not rerun for this release; the recorded requests and log matches can be read directly.

## Not included
6047 files, 26.1 MB of source files were left out. By category: run-state files: 2 files; credential-path file: 1 file; tarballs and archives: 1 file, 1.9 MB; raw data files over 2 MB: 2 files, 6.5 MB; per-request files for the 3,000-folder fixture: 6029 files, 12.3 MB; raw data files over 1 MB: 3 files, 5.0 MB; third-party documentation captures: 9 files, 0.2 MB. The largest omissions are the full request transcript `requests.jsonl` and the router request log. Raw logs and data are available on request.

## Edited for release
8 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. the lab hostname replaced by `labhost` in EVIDENCE-GUIDE.md, scripts/database.py and one run log; absolute home paths replaced by `$LAB_ROOT`, `$LAB_HOME` or `$HOME` in container-hostconfig.json and four provisioning logs. All are listed in SHA256SUMS.json. 8 of these 8 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
