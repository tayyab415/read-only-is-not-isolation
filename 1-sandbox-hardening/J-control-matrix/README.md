# J: control matrix

## Question
Read-only accounts still pass bits through download statistics and remembered upstream failures. Which single control closes which channel, and what does it cost? Paper Section 4.1.

## Setup
Artifactory OSS 7.161.26, image digest `sha256:d103c0464d33645acdcbfbb6fc61310d7faf14c9893975cbee562487eaeac1d1`. Eight repositories give seven configurations. Two scripted clients with Read-only accounts sit on separate internal networks behind the gateway, with a stateless upstream that answers missing paths 404 after 150 ms or 20 ms. A bit 1 means the sender requests a fresh path first. Thresholds were fixed on 12 balanced calibration trials per delay (88.28 ms and 23.04 ms) and never changed. Each arm got 32 balanced holdout timing bits per delay and 24 statistics bits. Campaign date 2026-11-08 UTC.

## Result
Recovered bits. Chance is 16/32 for timing and 12/24 for statistics.

| Configuration | Statistics (24) | Timing 150 ms (32) | Timing 20 ms (32) | Upstream calls |
| --- | --- | --- | --- | --- |
| Shared cache, GET/HEAD only | 24/24 | 32/32 | 32/32 | 32 |
| Storage API blocked | unavailable | 32/32 | 32/32 | 32 |
| Negative caching disabled | 24/24 | 16/32 | 16/32 | 48 |
| Both of the above | unavailable | 16/32 | 16/32 | 48 |
| Separate repositories | 12/24, always 0 | 16/32 | 16/32 | 48 |
| Local artifact storage disabled | unavailable | 32/32 | 32/32 | 32 |
| Repository offline, prewarmed | 24/24 | 16/32 | 16/32 | 0 |

- Receiver median in the baseline: 164.32 ms with a quiet sender, 11.76 ms after the sender primed the path. 16 no-sender trials per delay gave zero false positives.
- Disabling negative caching raises upstream calls from 32 to 48, 50% more on this workload.
- Under 320 background downloads at 20 ms the frozen decoder scored 25/32 and the combined-control arm 16/32. Upstream suppression still matched every sender bit.
- HEAD and conditional GET did not move the counter (8/16 each). A one-byte range GET did (16/16), sent directly to Artifactory because the gateway does not forward Range. Eight primes with the sender's credentials against the receiver's repository returned 401 (8/16).
- All 14 pinned `six==1.16.0` installs succeeded across the seven configurations, wheel SHA-256 `8abb2f1d...0254`.
- Log accounting: 1,919 forwarded requests each matched one Artifactory log entry, 1,946 client requests each matched one gateway entry. A mutation-method-only alert flags 0 of 256 sender requests.
- 80 of 80 protocol and evidence checks passed (`recheck/`). `data/analysis.json` has Wilson 95% intervals (24/24: 0.862 to 1.000; 32/32: 0.893 to 1.000; 16/32: 0.336 to 0.664). They are descriptive, under a Bernoulli assumption.
- Cost: about $0.062 of host compute (2,002.5 s at $0.1111/h, a rate estimate).

## Files
`PROTOCOL.md` predictions and method. `compose.yaml` clients, gateway, upstream. `scripts/`: `campaign.py` setup and main run, `followup.py` load, method and denied-prime tests, `identity.py` copied-credential test, `package_utility.py`, `collect.py`, `analyze.py`, `verify.py`, `gateway*.py`, `upstream*.py`. `data/`: schedules (`preregister.json`), `frozen-thresholds.json`, trial files (`timing-trials.jsonl`, `stat-trials.jsonl`, ...), `analysis.json`, `verification.json`, repository configs and grants, `logs/` (gateway, upstream, Artifactory). `recheck/` holds the output of an independent rerun of `analyze.py` and `verify.py`.

## How to rerun
The live campaign needs the AWS lab: a running Artifactory 7.161.26 at the pinned digest on host port 18584, Docker, and a private admin credential file. Order: `python3 scripts/campaign.py setup`, `docker compose up -d --pull never`, `python3 scripts/campaign.py run`, `python3 scripts/followup.py`, `python3 scripts/identity.py`, `python3 scripts/package_utility.py`, `python3 scripts/collect.py`. `analyze.py` and `verify.py` read `data/requests.jsonl`, `data/log-matches.json` and `data/bodies/` (all included) and run offline: `python3 scripts/analyze.py data` reproduces `data/analysis.json` exactly; `python3 scripts/verify.py data` passes 79 of 80 checks. The one failure is the evidence-manifest hash check, expected because of the files listed under "Edited for release" and the cost files left out.

## Not included
7 cost and resource files (price list, budget ledger, bundle hashes, audit and resource checks), the original README and follow-up notes, and the third-party `six` wheel. Raw logs available on request.

## Edited for release
`data/containers.json` (home paths) and `scripts/campaign.py` (one docstring said "hub", now "lab host") are listed in J's `SHA256SUMS.json` or bundle manifest, so those hashes refer to the originals. `PROTOCOL.md`: one budget sentence reworded to drop a tool name.
