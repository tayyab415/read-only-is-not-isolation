# P3: frozen admission and private immutable serving

## Question
Can a trusted step admit and hash-check a dependency set in advance, and private per-tenant servers then serve it, so that ordinary installs work while a read changes nothing another tenant sees? Paper Section 4.7.

## Setup
Three parts, kept as sibling folders because the scripts import each other by relative path.
- `P3-mirror/` is the first edition and the paper's 28 installs. `admit.py` fetched 24 universal wheels for 14 pinned root packages and checked each against the SHA-256 in PyPI's versioned JSON. `mirror.py` is a static index with no request-triggered upstream fetch. `utility.py` ran two fresh dependency-resolving installs per root through two separate serving processes, pip cache off, on macOS with CPython 3.11.4 and pip 25.3. A first admission attempt timed out on one download and is kept in `failed-admission-01/`.
- `P3-isolation/deploy.py` is edition 2: one listener per tenant on that tenant's private network, identity set by the network origin, no password check.
- `P3-linux/` reruns the same 28 tasks on Linux for two routes behind one index adapter: separate Artifactory repositories, and private immutable serving. Second lab VM, Python 3.12.16, pip 25.0.1, 256 MiB, 128 MiB private temp, 0.5 CPU.

## Result
- macOS, `P3-mirror`: 28/28 installs and package-function checks. 24 wheels, 5,013,675 bytes. 54 logged requests per client, 108 in all, every one 200. Median task 1.18 s, range 1.13 to 2.03 s. 176 audit checks passed. Wheel and manifest hashes unchanged after the run. Manifest SHA-256 `7ca3d6b4...5321`.
- Eight implementation checks passed (altered wheel rejected, path confinement, unknown and metadata paths fail locally, repeated reads give identical responses, HEAD and conditional GET, ranges, writes rejected, index fields). They are functional checks, not a covert-channel search.
- Linux, `P3-linux/data/u1`: 28/28 for separate repositories and 28/28 for immutable serving. Median task 4.308 s and 4.288 s. Median matched difference, immutable minus separate, -8.35 ms, bootstrap 95% interval -25.00 to +7.99 ms, so no speed difference is shown. 108 index and wheel requests per arm, 54 backend wheel requests, 10,274,240 payload bytes. Trusted setup fill requests 48 versus 0. Audit checks 678 and 329, none failed. Paper Section 4.7 reports only the macOS run.
- Scope: pure-Python wheels, 14 roots, two tenants. No native extensions, source builds or timing evidence.

## Files
`P3-mirror/PROTOCOL.md` written before retrieval. `admit.py`, `mirror.py`, `utility.py`, `audit.py`, `test_mirror.py`. `data/manifest.json`, `utility-results.json`, `utility-audit.json`, `service-*.jsonl`, `utility/` (per-task pip reports and function output). `P3-linux/`: `PROTOCOL.md`, `UTILITY-FROZEN.json` and two amendments, `adapter.py`, `client.py`, `deployment.py`, `audit.py`, `report.py`, tests, `data/u1/{separate,immutable}/`.

## How to rerun
`python3 -m unittest test_mirror` in `P3-mirror` (8 tests) and `python3 -m unittest test_deploy` in `P3-isolation` (6 tests) pass from this copy. `admit.py` needs network access to PyPI, then `python3 utility.py` and `python3 audit.py`; `audit.py` needs the wheels and PyPI captures that are not included, and I did not rerun these. `P3-linux` needs the AWS lab, the `P1-meter` and `P2-residual` sources in the leakage-measurement section, and the wheels.

## Not included
`P3-mirror`: 48 files, 5.4 MB (24 third-party wheels, 24 PyPI metadata captures). `P3-linux`: 34 files, 0.4 MB (superseded editions in `archive/`, 4 tarballs, 2 readiness notes, bytecode). Original READMEs replaced. Raw logs available on request.

## Edited for release
Emails and home paths removed from pip reports and logs in `P3-mirror/data` and `failed-admission-01`. In `P3-linux`, edits to `PROTOCOL.md`, `UTILITY-FROZEN.json`, `deployment.py` (a "hub" string) and both `data/u1/*/tasks.jsonl` (emails inside pip metadata) mean the hashes in `UTILITY-FROZEN.json`, `DEPLOYMENT.json`, `deployment-preparation-manifest.json` and the `sealed.json` files refer to the originals.
