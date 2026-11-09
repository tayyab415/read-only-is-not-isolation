# S3: how many bits per second does the load channel carry under the gateway salt?

Frozen 2026-11-09 after S1 and S2 results and a 20-symbol-per-arm smoke test (trial IDs `s3-smoke-*` in `logs/gw-s3-salted.jsonl`, not analysed). Same server and model as S1 (`serve.sh cache_on`, vLLM 0.33.0, Qwen2.5-1.5B-Instruct, one L4). Gateway: the S1 salted gateway on port 9002, logging to `gw-s3-salted.jsonl`.

## Question

S1 showed that one ordinary request from tenant A is visible to tenant B under a gateway-enforced cache salt. A leakage budget needs a rate, not just per-trial accuracy at an artificial pace. S3 measures achievable bits per second for a simple scripted scheme, with no cache channel available.

## Design (`trials_s3.py`)

A continuous symbol stream with period T. At each slot start, tenant A sends one ordinary request if the bit is 1 (400 random words, about 975 prompt tokens, 1 output token) and nothing if it is 0. 10 ms after slot start, tenant B sends a fresh 100-word probe (1 output token) and records its latency. Requests run in threads, so the slot clock keeps time and slow requests can overlap the next slot (inter-symbol interference is part of the measurement). The two tenants share a clock because the same controller runs both. Real agents would need synchronisation, for example from wall clocks; that assumption is stated, not tested.

| Arm | Period T | Background |
| --- | --- | --- |
| T50 | 50 ms | none |
| T100 | 100 ms | none |
| T200 | 200 ms | none |
| T400 | 400 ms | none |
| T200_busy | 200 ms | tenant C, 4 threads of ordinary requests (400 words, 32 output tokens), S1's `Busy` |

Calibration 200 symbols per arm, holdout 600 symbols per arm, balanced bits, blocks of 50 consecutive symbols, arms interleaved in seeded random block order. Decoder: one latency threshold per arm, higher latency means bit 1, fitted on calibration with S1's `fit_threshold` and frozen to `data/thresholds-s3.json` before holdout.

## Outcomes (`analyze_s3.py`, holdout only)

Per arm: accuracy with failed probes counted as errors; Wilson 95% interval; exact binomial p; AUC; median latency per bit; bits per symbol 1 − H2(accuracy) at the point estimate and at the Wilson lower bound (0 if at or below 0.5); and bits per second, that value divided by T. Audit: one receiver record per symbol, a sender record exactly when the bit is 1, tenants and salt presence correct.

This measures one simple scheme. Better coding (multi-level symbols, error correction, adaptive thresholds) could carry more. The figures are lower bounds on what the channel allows for this scheme and workload, not channel capacity.

## Budget

No model API calls. GPU VM time only (about 15 minutes of collection), within the $20 ceiling and the VM's self-terminate window.
