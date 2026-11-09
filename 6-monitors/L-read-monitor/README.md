# L: monitor for read effects

## Question

Can an offline monitor that sees only completed server records find the read-triggered state changes behind the statistics and miss-timing channels?

## Setup

- The monitor accepts event ID, principal, method, full path, start, duration, status and response size. It rejects ground-truth fields and needs records in completion order. A producer must finish before the observer starts, within 600 s.
- Four candidates: statistics observations (`?stats` reads, which needs the query string), prior download by another principal followed by a statistics read, repeated cross-principal misses on one path, and failed artifact requests faster than 80 ms. A shared principal cannot produce cross-principal attribution. Cache aliases map to their repository through explicit configuration.
- Inputs are study H's verified requests and study J's trusted gateway records. Study H's Artifactory request log drops the `?stats` query string, so the router record is used.

## Result

On H's 433 verified requests the monitor produces 169 statistics observations, 19 cross-principal download and statistics associations, 32 miss associations and 24 failed artifact requests under 80 ms. After a statistics read there were 15 candidate exposures among 32 shared trials, exactly the 15 sender-download trials. The 16 no-sender and 32 separate-object trials had none. Stripping the query string removes every recognizable statistics observation.

H's preselected 80 ms threshold, reused on router durations without retuning, classifies 16/16 default-negative-cache bits, against 10/16 on client durations. With negative caching disabled both classify 8/16.

On J's 1,946 trusted gateway records, after 2,045 response hashes and 14 pinned installs were re-verified, the monitor produces 433 statistics observations, 2,394 download and statistics associations, 288 miss associations and 594 fast-failure candidates. Past downloads stay candidate exposures on later zero trials, so large counts do not mean transmitted bits. Fast failures also occur when an offline repository does no upstream work.

Serial schedule rates: H statistics 32 symbols in 79.9 s (about 0.40 symbols/s); J statistics 24 in 22.6 s (about 1.06/s); J timing 32 in 22.0 s at 150 ms (about 1.46/s) and 18.4 s at 20 ms (about 1.74/s). These include controller overhead and are not capacities.

A candidate exists only after its request completes. That is an evidence-availability bound. No alert delivery latency or operational false-positive rate was measured.

## Files

| File | What it is |
| --- | --- |
| `monitor.py` | `ReadMonitor`. |
| `replay.py`, `verification.json`, `alerts.jsonl`, `server-only.jsonl` | Replay over H's requests and its saved output. |
| `check_controls.py`, `controls-verification.json`, `controls-alerts.jsonl`, `controls-server-only.jsonl` | Same for J's gateway records, with source hashes. |
| `rates.py`, `rates.json` | Schedule rates. |
| `runtime-readback.json` | Check that the H, J, M and MT test clients were removed. |

## How to rerun

`monitor.py` has no dependencies and can be imported directly. `replay.py` reads `../H-infra/data`, `check_controls.py` reads `../J-controls/data`, and `rates.py` reads both. Those siblings are the original lab layout (`artifactory-lab/evidence/`) and their raw data are not in this section, so the three scripts will not run from this folder alone. The saved outputs can be read as they are. None of the scripts makes network, AWS or model calls.

## Not included

Nothing from the source folder (12 of 12 files, 2.2 MB). H and J raw request logs are elsewhere in the lab; raw logs available on request.

## Edited for release

None.
