# C: server logs and detection

## Question
How well do server-side logs separate message-board use from benign use, and what do the logs contain?

## Setup
- Artifactory OSS 7.161.26 with PostgreSQL in Docker. Eight scripted clients, each with its own user and source IP on a Docker network: 2 benign, 4 board, 2 mixed. No language models.
- Main run: 75 minutes from 2026-11-07 23:33:50 UTC, 4,700 requests, of which 3,585 (76%) are board use. Two phases: frequent regular polling, then less frequent and more jittered polling.
- Three short runs: a property channel on a local repository (8 min), a heartbeat after the agent exits (16 min), and a read-only user trying writes. Detectors read only server-side fields; ground truth comes from client-side logs joined on a `lab/<client>/<seq>` token in the User-Agent.

## Result (from the study report)
- Cache writes: `MKCOL` into a `*-cache` repository flagged 1,632 of 1,632 board writes with no false flags (precision 1.000, recall 1.00). Any `MKCOL` flagged 1,642 with 10 false (benign publishes). The zero depends on benign clients never writing to a cache, so a real deployment needs a baseline.
- Repeated listing: 5 or more listings of one path in 10 minutes, cache repositories only, had precision 1.000 and recall 0.870 (1,699 of 1,953 board listings). Over all directories precision fell to 0.921 (a benign 30-second update checker). The two mixed clients, listing every 4 to 8 minutes, were never flagged.
- Regularity: low polling variance (CV below 0.5) flagged cache listings with precision 1.00, recall 0.97. Exponential jitter took the flag rate to about 0 to 4%; if a quarter of the sleeps return at once it was 7 to 42%.
- Chains: timing alone gave pair-level AUC 0.83 (0.87 in a second null draw). For answers, the true sender was among the candidates 51 of 51 times (median 3); picking the most recent poster was right 52.9%. With exact path equality as an opaque key, the receiver was identified exactly for 91.9% of 160 directed messages.
- Properties: writing properties to the cache did not work in OSS (25 of 25 `PUT` returned 404). On a local repository it works, and polling `/api/search/prop` involves no directory listing, so listing-based detectors miss it.
- Log quirks: the request log drops query strings and records HTML listings as `/api/nativeBrowser`; successful `MKCOL` is absent from `artifactory-access.log`. A monitor needs the router log joined on trace id.
- Limits: scripted clients, one seed, 160 messages, no confidence intervals; request-level scores are inflated by the 76% board share.

## Files
- `scripts/`: client, launcher, provisioning, property and after-exit runs, log-sample script.
- `analysis/`: dataset builder, detectors (`detect.py`), sensitivity, thinning, per-client summary.
- `data/`: labelled datasets (`ds-main`, `ds-props`, `ds-props3`, `ds-afterexit`, one row per request) and the result files quoted above (`results-main.json`, `sensitivity.json`, ...).
- `logs-sample/`: readable excerpts of each server log. `image.txt`: image tag and digest. `EVIDENCE-GUIDE.md`: dataset columns and commands.

## How to rerun
Needs Python 3 with numpy and pandas. From `analysis/`: `python3 -I detect.py ../data/ds-main 2250 /tmp/results-main.json` (also `clientsummary.py`, `thin.py`, `sensitivity.py`, `props_eval.py`, `afterexit_eval.py`; see EVIDENCE-GUIDE.md). I reran `detect.py` on the shipped dataset and the output equals `data/results-main.json`. Rebuilding datasets with `build_dataset.py` needs the raw logs, which are not included. Generating traffic needs a Docker host with Artifactory; not rerun for this release.

## Not included
11 files, 2.2 MB of source files were left out. By category: tarballs and archives: 3 files, 2.2 MB. The three archives are the client ground-truth logs and the full server logs of the run. Raw logs and data are available on request.

## Edited for release
2 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. the lab hostname replaced by `labhost` in EVIDENCE-GUIDE.md and scripts/provision.py. 0 of these 2 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
