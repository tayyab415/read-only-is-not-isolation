# H: first channel measurement

## Question
With accounts that can only read, does one sandbox's download change what another sandbox can see through download statistics or remembered misses?

## Setup
Four small containers (sender, receiver, upstream, target) on three internal networks, each non-root, read-only root, 128 MiB (`compose.yaml`, `gateway_mode_ipv4: isolated`). Artifactory OSS 7.161.26. Sender and receiver are separate Artifactory users. The statistics test has 32 shared-path bits, 16 no-sender controls and 32 separate-path controls, with the plan written before the trials. The timing test has 16 bits in each of two caching conditions. Run on 2026-11-07 and 08 UTC.

## Result
- Statistics, shared path: 32/32 bits recovered, 15 of them ones. No-sender controls: 0 of 16 positive. Separate paths: 0 of 32 positive (17/32 match the schedule, which is what an always-zero decoder scores).
- Timing: default negative cache 10/16 correct, 2 decoded as one. Negative caching disabled: 8/16 correct, none decoded as one. The threshold was weak and was left unchanged afterwards. J repeated this with a calibrated threshold and got 32/32 (see `../../1-sandbox-hardening/J-control-matrix/`).
- Client medians in the default condition were 94.6 ms for bit 1 and 246.7 ms for bit 0. Server-side medians were 2.5 ms and 155.1 ms.
- Remote repository: a read-only user fetching an uncached path got 403 "not permitted to deploy", while a path already in the cache returned 200. On a cached object, `downloadCount` went from 2 to 3 and `lastDownloadedBy` changed from `h-remote2` to `h-remote1` (`data/remote-stat-summary.json`).
- 433 of 433 requests matched Artifactory and router log entries, and all 433 response body hashes verified. Direct-connectivity, cleanup and redaction checks passed.
- Neither throughput nor an autonomous discovery rate was measured.

## Files
`compose.yaml`, `analyze.py`. `scripts/`: `probe.py` (fixtures), `stat_channel.py`, `remote_channels.py`, `remote_followup.py`, `collect.py`, `connectivity.py`, `cleanup.py`, `verify_cleanup.py`, `verify_redaction.py`, `fake_upstream.py`. `data/`: `requests.jsonl`, `log-matches.json`, `bodies/`, `logs/`, the preregistered plans, trial files, summaries and `verified-summary.json`.

## How to rerun
`python3 analyze.py` in this folder recomputes `data/verified-summary.json` from the included data. I ran it on a copy and the output matched the included file byte for byte. The live scripts need the lab Artifactory and its private credential files, and must not be pointed at another service. Use fresh fixture names; reusing downloaded artifacts changes the counters.

## Not included
The original README (replaced by this one). Raw logs available on request.

## Edited for release
`data/containers.json`: home paths in bind mounts changed to `/home/<user>`. Nothing here is hashed by a manifest.
