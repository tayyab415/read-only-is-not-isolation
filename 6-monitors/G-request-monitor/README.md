# G: offline request monitor

## Question

Can a causal rule that sees only completed server request records flag cache and property writes, possible exposure of a write to another principal, and repeated cache reads?

## Setup

- The monitor reads one projected record per request: event ID, authenticated principal, method, path, start, duration, status and response size. It rejects any field that carries a message label, and it needs records sorted by request start.
- The input is 1,010 requests from an earlier scripted-client run (study C), frozen as `server-only.jsonl` with its SHA-256 in `cohort.json`. That run's own labels are not in this folder.
- Three rules fire. `cache_or_property_mutation` marks a write method on a `*-cache` repository or a property view. `possible_parent_listing_exposure` marks a different principal listing the parent directory within 600 s after a successful write completes. `repeated_cache_read_candidate` marks at least 5 reads of one path by one principal in 600 s. The thresholds are illustrative.

## Result

The saved replay raises 6,512 candidate alerts: 360 mutations, 5,740 possible parent-listing exposures and 412 repeated cache reads. `verify.py` checks that the alerts equal a fresh replay, that alerts on a prefix of the stream equal the alerts a longer run gave for the same events, that label fields are rejected, that a write completes before any exposure alert, and that a shared identity abstains from cross-principal attribution.

These are candidates from scripted, selected requests. No coordination recall, precision or false-positive rate is computed. A possible listing exposure does not show that the response contained the new entry or that anyone read it. The rules do not cover the local-artifact statistics channel from study H; study L does.

## Files

| File | What it is |
| --- | --- |
| `monitor.py` | The monitor and the field allowlist. `python3 monitor.py INPUT ALERTS SERVER_ONLY` rebuilds both outputs from a joined request file. |
| `verify.py` | Offline checks listed above. |
| `server-only.jsonl` | The 1,010 frozen server projections. |
| `cohort.json` | Provenance note and SHA-256 of `server-only.jsonl`. |
| `alerts.jsonl` | The 6,512 saved alerts (2.5 MB). |
| `verification.json` | Output of `verify.py`, with rule counts and stated limits. |

## How to rerun

`verify.py` uses the path `artifactory-lab/evidence/G-monitor` relative to the working directory, and it rewrites `verification.json` there. It needs no network, AWS or model calls. In a scratch directory:

```sh
mkdir -p lab/artifactory-lab/evidence && cp -r G-request-monitor lab/artifactory-lab/evidence/G-monitor
cd lab && python3 artifactory-lab/evidence/G-monitor/verify.py
```

This completes in a scratch copy. Rebuilding `server-only.jsonl` with `monitor.py` needs study C's joined request file, which is not in this repository.

## Not included

- The original `README.md` (1 file, 1.4 KB), replaced by this one.
- Study C's request data and labels. Raw logs available on request.

## Edited for release

None.
