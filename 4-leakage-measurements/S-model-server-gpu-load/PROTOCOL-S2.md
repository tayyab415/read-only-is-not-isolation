# S2: does a response-time floor remove the load channel left by salting?

Written and frozen 2026-11-09 while S1 holdout was collecting, from S1 smoke and calibration only. No S1 holdout result was seen when S2 was designed.

## Question

S1 calibration showed that a gateway-enforced cache salt does not affect the load channel: when tenant A runs a long request, tenant B's request slows from about 58 to 420 ms. A common response is to pad responses to a minimum time. A fixed floor hides only requests that would finish before it. The receiver chooses its own request size, so it can send requests that finish after any fixed floor. S2 measures both sides on the same server.

## Design

Same server and model as S1 (`serve.sh cache_on`). Gateways for this phase log to separate files:

- port 9002: the S1 salted gateway (`gateway.py --policy salted`), log `gw-floor-salted.jsonl`;
- port 9004: `gateway_floor.py --policy salted --floor-ms 500`, log `gw-floor-floor500.jsonl`. It is the S1 gateway plus one change: each response is held until 500 ms after the request arrived. Slower responses are unchanged.

Every arm uses the S1 heavy load: b = 1 means tenant A starts a 2,400-word request (about 6,000 tokens, `max_tokens` 1); b = 0 means tenant A sends nothing. Tenant B sends a fresh 400-word prompt 10 ms later and times it.

| Arm | Gateway | Receiver output | Prediction |
| --- | --- | --- | --- |
| floor500_short | floor 500 ms | 1 token | Near chance: loaded latency in S1 smoke (about 420 ms) is under the floor |
| floor500_long | floor 500 ms | 64 tokens, `ignore_eos` | Above chance: the request finishes after the floor, so load shows |
| nofloor_long | salted, no floor | 64 tokens, `ignore_eos` | Above chance (baseline for long requests) |

Calibration 80 trials and holdout 240 trials per arm, balanced bits, blocks of 20 interleaved in seeded order (S1's `schedule`). Threshold: S1's `fit_threshold`, with higher latency meaning b = 1, frozen to `data/thresholds-floor.json` before holdout.

Utility: 30 sequential requests each of 1, 32 and 128 output tokens (400-word prompts, `ignore_eos`) through each gateway on an otherwise idle server. Reports mean added latency from the floor.

## Outcomes

Per arm, holdout: accuracy with Wilson 95% interval, exact binomial p against 0.5, AUC with bootstrap interval, and the capacity bound, computed as in S1 (`analyze_s2.py`). Audit: one receiver record per trial in the gateway log, the expected sender records, tenant and floor fields.

## Stop rules

S2 makes no model API calls. It runs only after S1's `run_all.sh` prints `S1_DONE`, within the VM's existing self-terminate window and the $8 VM limit.
