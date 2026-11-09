# S: tenant isolation on a shared model server

## Question
On a vLLM server we own, what can a second tenant read about the first tenant's activity, does a gateway-assigned cache salt stop it, and what does that cost ordinary agent traffic?

## Setup
- AWS g6.xlarge (NVIDIA L4), vLLM 0.33.0, `Qwen/Qwen2.5-1.5B-Instruct`. Clients and server on one VM, reaching vLLM only through gateways written for the study. 2026-11-09, 00:40 to 01:08 UTC for S1. VM terminated 02:03 UTC; about $1.49 compute plus $0.02 storage at list prices.
- Gateway policies: shared cache, gateway-assigned salt (HMAC of tenant identity, client salt stripped), client-chosen salt, prefix caching off. The gateway strips cached-token counts from client responses and logs them itself.
- S1: 120 calibration and 400 holdout trials per arm, one frozen latency threshold per arm. S2: 500 ms response floor, 240 holdout trials per arm. S3: symbol stream, 600 holdout symbols per arm, 200 calibration. S4: two vLLM processes on one GPU.
- Protocols and hashes were frozen before each data set (`PROTOCOL*.md`, `FREEZE*.json`).

## Result (from the S1 report and paper fragment `new-direction-S1.tex`)
| S1 arm (400 trials) | Accuracy (Wilson 95%) | Receiver cached tokens, b=0 to b=1 |
| --- | --- | --- |
| Shared cache | 1.000 (0.991 to 1.000) | 16 to 960 |
| Client-chosen salt, same value | 1.000 (0.991 to 1.000) | 16 to 960 |
| Shared, about 100-token prefix | 0.968 (0.945 to 0.981) | 16 to 112 |
| Shared, busy server | 0.830 (0.790 to 0.864) | 16 to 960 |
| **Gateway-assigned salt** | **0.498 (0.449 to 0.546)** | 16 to 16 |
| Gateway salt, busy server | 0.503 (0.454 to 0.551) | 16 to 16 |
| Prefix caching off | 0.485 (0.436 to 0.534) | 0 to 0 |
| Gateway salt, one ordinary 400-word request of load | 1.000 (0.991 to 1.000) | 16 to 16 |
- All 3,600 S1 holdout trials joined to the gateway log. The salt removed the cache channel; it does not touch the load channel (receiver median 54.9 to 102.6 ms after one ordinary request).
- Cost: with tenant-own scaffolds, salt vs shared mean latency 540.1 vs 542.5 ms (14.72 vs 14.66 requests/s); with a common scaffold 539.1 vs 528.3 ms. Caching off: about 1,151 ms and 6.9 requests/s. Three repetitions, 96 of 96 requests succeeded.
- S2: the 500 ms floor with 1-token receiver requests gave accuracy 0.421 (0.360 to 0.484), AUC 0.399; with 64-token requests the floor changed nothing (1.000).
- S3 (gateway salt, idle server): 1.000 accuracy at 100, 200 and 400 ms symbols, 10.0 bits/s at 100 ms (Wilson lower bound 9.4); 0.778 at 50 ms; busy server 0.735 at 200 ms (0.83 bits/s).
- S4: separate processes on one GPU still decoded 1.000 at 100 ms (10.0 bits/s, lower bound 9.4). Achieved rates for one simple scheme, not capacity.
- Limits: one GPU, one small model, synthetic prompts, scripted clients, direct access to port 8000 not tested.

## Files
- `PROTOCOL*.md`, `FREEZE*.json`; code: `gateway.py`, `gateway_floor.py`, `trials*.py`, `utility.py`, `analyze_s*.py`, `figure_s1.py`, `s*_run.sh`; `analysis*.json`; `trial-pace-posthoc.json`.
- `data/` (trials, frozen thresholds, utility), `logs/` (gateway and vLLM logs), `VM/` (launch and serve scripts, environment, teardown record), `results/` (figure).

## How to rerun
Needs an NVIDIA GPU (the study used one L4) with vLLM 0.33.0 and the model; `VM/launch.py` shows the AWS launch and `VM/serve.sh`, `s*_run.sh` the runs. `trials.py` reads gateway tenant keys from `~/s1/tenant-keys.json`; that secret file is not published, so create your own. `analyze_s*.py` also needs the three large gateway logs listed below, so the analysis can only be rerun in full with them.

## Not included
14 files, 11.2 MB of source files were left out. By category: run-process notes: 1 file; node inventory with IPs: 1 file; raw data files over 2 MB: 3 files, 10.2 MB; empty log files: 6 files; raw data files over 1 MB: 1 file, 1.0 MB. The omitted raw files are three gateway logs of 3.3, 3.0 and 3.9 MB (shared and salted S1, and S3) and one vLLM log of 1.0 MB. The VM node inventory (with IPs) was dropped, and the gateway tenant keys were never copied. Raw logs and data are available on request.

## Edited for release
1 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. In VM/launch.py the SSH public-key filename was changed to `aws_lab`. 0 of these 1 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
