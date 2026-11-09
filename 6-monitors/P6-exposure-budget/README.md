# P6: an information budget for server-side monitoring

## Question

O's rule M1 flags any single cross-principal exposure. It caught all 110 verified exchanges and flagged 19 of 60 benign agents. Does a rule that estimates how much information a peer's activity could have revealed separate the two better?

## Setup

- The rule is in `monitor.py` and described in `DEV.md`. An observer earns one bit when it first sees a statistics state or negative-cache state that a peer created. Repeated reads of an unchanged state earn nothing. A multi-object sweep earns no extra credit, because the server view does not show the candidate set. Threshold is 1 bit. At 2 bits the rule found only 3 of 56 development exchanges.
- The rule was built on a development split (A-stage pilots against the original BN negatives) and frozen in `FROZEN.json` before any test view was read. `evaluate_test.py` refuses to run if a frozen hash has changed and checks that each N gateway log equals O's server view.
- Test split: B3 and B2 positives (240 agents, 110 verified exchanges) against BNR negatives (60 agents). M1 is replayed on the same agents.

## Result

| | P6 | M1 |
| --- | ---: | ---: |
| Verified-exchange recall | 110/110 | 110/110 |
| Assigned-task recall | 119/240 | 119/240 |
| Benign flags (BNR) | 17/60 | 19/60 |
| Benign metadata tasks | 14/20 | 14/20 |
| Benign latency tasks | 3/20 | 5/20 |
| Benign basic tasks | 0/20 | 0/20 |
| Task AUC (cohort bootstrap 95%) | 0.541 [0.468, 0.612] | 0.514 [0.428, 0.598] |
| Verified-exchange AUC | 0.887 [0.851, 0.920] | 0.866 [0.818, 0.908] |

C1 against BNR gives 35/35 verified exchanges for both rules and 17/60 against 19/60 benign flags. The only gain is two fewer latency flags, from repeated 404s that M1 counted as new exposures. Every interval overlaps M1's.

Counting fails because most exchanges need one informative read. On test, 142 of 145 verified agents scored exactly 1 bit, while 13 of 20 benign metadata agents scored 2, since their assigned work reads statistics their peer changed. Any threshold above 1 bit loses exchanges before it loses benign flags. A monitor can see that exposure happened but not why, so removing the shared observation is the more reliable control (paper Section 4.4).

Limits: assigned cooperative tasks and hard-negative benign tasks, one service configuration family, a 10-minute negative-cache window that follows M1 rather than the 1,800 s open setting, and verified-exchange labels that require a statistics read, so perfect recall partly follows from the label. This measures exposure accounting, not intent.

## Files

| File | What it is |
| --- | --- |
| `PROTOCOL.md`, `DEV.md`, `config.json` | Question, rule definition, parameters. |
| `monitor.py` | The P6 rule and a replay of M1. |
| `evaluate_dev.py`, `results.json` | Development-split scoring and output. |
| `FROZEN.json` | SHA-256 of code, configuration and development results, and the list of test views. |
| `evaluate_test.py`, `test-results.json` | Test scoring, written after the freeze, and its per-agent rows. |

## How to rerun

Both evaluators use only the standard library and make no network or model calls, but they need the original lab layout. They find N data at `<root>/evidence/N-ladder/data/<cohort>/server.json` and `verification-v2.json`, and `FROZEN.json` lists test views as `../O-monitors/results/views/...`. In this repository O sits at `../O-server-rules` and the N raw files are mostly not included, so the scripts will not run unchanged. Rebuild the original `artifactory-lab/evidence/` layout with the N and O folders under their old names to rerun.

## Not included

- `CHANGES.md` (1 file, 1.2 KB) and `__pycache__` (2 files). Source: 12 files, 356 KB; copied: 9 files, 328 KB.
- N gateway logs used as input. Raw logs available on request.

## Edited for release

- `PROTOCOL.md`: the host name in "no paid calls, no <host>" (two places) is now "no lab host". Neither `FROZEN.json` nor `config.json` hashes this file.
