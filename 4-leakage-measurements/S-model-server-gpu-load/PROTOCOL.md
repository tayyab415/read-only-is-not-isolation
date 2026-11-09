# S1: tenant isolation on an owned model server

Frozen 2026-11-09 before calibration. Smoke trials on 2026-11-09 (16 per condition, gateway log trial IDs `smoke-*`) were used to set prompt lengths and check the joins. They are not results.

## Question

When agent clients from different tenants share one inference server, does one tenant's ordinary request activity change what another tenant can measure? Which server-side controls remove that, and what do they cost ordinary traffic?

Prompt-cache timing is published prior work (see `reports/S-T-prior-art.md`). The contribution here is a measured deployment control for agent fleets, its residual channel and its utility cost, not the cache mechanism.

## Setup

```text
tenant A client ─┐
tenant B client ─┼─ owned gateway (one per policy) ─ vLLM 0.33.0 (127.0.0.1:8000)
tenant C client ─┘
```

- One AWS g6.xlarge (NVIDIA L4 24 GB), us-east-1, no IAM role, SSH from the controller IP only, self-terminating. Details in `VM/instance.json`.
- Model `Qwen/Qwen2.5-1.5B-Instruct`, bf16, `--max-model-len 8192 --gpu-memory-utilization 0.85 --seed 0 --enable-prompt-tokens-details`. Prefix caching on (`cache_on`) or off (`cache_off`); chunked prefill on (vLLM default). Exact command: `VM/serve.sh`.
- Gateways (`gateway.py`): tenants authenticate with bearer keys. Policies, fixed per process:
  - `shared` (port 9001): strips any client `cache_salt`, injects none.
  - `salted` (9002): strips any client salt and injects HMAC-SHA256(gateway secret, tenant ID), 64 hex characters.
  - `passthrough` (9003): forwards the client's salt.
- The gateway removes `prompt_tokens_details` from client responses. Cached-token counts appear only in the gateway's trusted JSON log. Timing is the only receiver-visible signal.
- Clients are scripted, run on the same VM, and use separate HTTP clients and keys. The receiver's decision uses only its own wall-clock latency.

Source check, vLLM 0.33.0: `cache_salt` field description in `entrypoints/openai/chat_completion/protocol.py` (lines 507 to 519); salt added to the extra keys of the first block hash in `v1/core/kv_cache_utils.py` (lines 659 to 669), so every later block hash depends on it through chaining.

## Trials (`trials.py`)

Each trial has a bit b from a balanced, seeded schedule. Text is random four-letter words, generated fresh per trial from the trial ID.

Cache arms: tenant A sends P (if b = 1) or Q (if b = 0), both 400 words, about 975 prompt tokens, `max_tokens` 1. After 50 ms tenant B sends P plus "Reply with one word." and records its latency. Q equalises the sender's load, so only the cache state differs.

Load arms: if b = 1, tenant A starts a long request (2,400 words, or 400 for the light arm); 10 ms later tenant B sends a fresh 400-word prompt. If b = 0, tenant A sends nothing.

| Phase | Arm | Gateway | Purpose |
| --- | --- | --- | --- |
| cache_on | shared | shared | Baseline shared cache |
| cache_on | salted | salted | Gateway-enforced tenant salt |
| cache_on | client_salt | passthrough, both tenants send the same salt | Client-chosen salt between cooperating tenants |
| cache_on | shared_short | shared | 40-word (about 100-token) prefix |
| cache_on | shared_busy | shared | Tenant C runs 4 threads of ordinary requests (400 words, 32 output tokens) during the block |
| cache_on | salted_busy | salted | As above, salted |
| cache_on | contention | salted | Heavy sender load; salting does not target this |
| cache_on | contention_light | salted | Sender load of one ordinary 400-word request |
| cache_off | nocache | shared | Prefix caching disabled |

Each arm gets 120 calibration trials, then 400 holdout trials, in blocks of 20 with arms interleaved in seeded random order. Ten warm-up requests per tenant per gateway precede each split and are not trials.

Decoder: one latency threshold per arm, the first maximum of calibration accuracy over midpoints of sorted calibration latencies. Direction is fixed in advance: lower latency means b = 1 for cache arms, higher latency means b = 1 for load arms. Thresholds are written to `data/thresholds-<phase>.json` with the calibration file hash before any holdout trial runs. Each split is collected once.

## Outcomes (`analyze_s.py`, holdout only)

- Accuracy of the frozen threshold, Wilson 95% interval, exact two-sided binomial p against 0.5.
- AUC (threshold-free) with a 2,000-resample bootstrap interval.
- Capacity bound: 1 − H2(lower Wilson bound) bits per trial, or 0 when the bound is at or below 0.5, and holdout trials per second. This bounds what this scripted decoder achieves. It does not certify that no better decoder exists.
- Native evidence: each trial must have one receiver record and the expected number of sender records in the gateway log, with the right tenant, policy and salt presence. Receiver cached tokens by bit. A cross-tenant hit is a receiver record with more than 64 cached tokens; the chat template alone caches 16.

Expected from smoke: shared and client_salt decode near 1.0; salted near 0.5; contention near 1.0. Any arm can contradict these.

## Utility (`utility.py`)

Two tenants × 4 agent threads × 12 requests. Each request is a 600-word scaffold system prompt (about 1,500 tokens) plus a fresh 40-word turn, 32 output tokens with `ignore_eos`. Workloads: `distinct` (each tenant has its own scaffold) and `common` (both tenants share one scaffold). Configurations: shared, salted (cache_on) and nocache (cache_off). Three repetitions each in seeded random order. Measures: mean and p95 request latency, requests per second, and the cached share of prompt tokens from the gateway log.

## Budget and stop rules

The VM costs $0.8048 per hour on demand and self-terminates 8 hours after launch (about 08:12 UTC). S1 makes no model API calls. If the VM bill would pass $8, stop. Failed requests stay in the data. The VM is terminated, and its security group and key pair are deleted, after evidence is copied back.
