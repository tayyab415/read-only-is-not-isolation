# S4: does a separate model-server process per tenant on the same GPU stop the load channel?

Frozen 2026-11-09 after S1 to S3 results, before any S4 data. Requested as a control by `reports/LITERATURE-REVIEW.md` ("separate-instance ... controls").

## Design

Two vLLM 0.33.0 processes serving `Qwen/Qwen2.5-1.5B-Instruct` share one NVIDIA L4: instance A on 127.0.0.1:8000 and instance B on 127.0.0.1:8001, each `--gpu-memory-utilization 0.42`, prefix caching on (`s4_run.sh`). Tenant A uses only instance A through a salted gateway on port 9101 (log `gw-s4-a.jsonl`); tenant B uses only instance B through a salted gateway on port 9102 (log `gw-s4-b.jsonl`). The processes share no cache, scheduler or batch. They share only the GPU, host and PCIe bus.

The S3 symbol stream is reused unchanged (`trials_s3.run_block`). A 1-bit means tenant A sends one ordinary 400-word request at slot start; tenant B sends a 100-word probe 10 ms later and records its latency. Arms T100_sep and T200_sep. Calibration 200 and holdout 600 symbols per arm, blocks of 50, seeded order. One latency threshold per arm (higher means 1), frozen to `data/thresholds-s4.json` before holdout.

## Outcomes (`analyze_s4.py`)

As S3: accuracy (Wilson 95%), exact binomial p, AUC, median latency per bit, bits per symbol and per second at the point estimate and the Wilson lower bound. Audit: one receiver record per symbol in `gw-s4-b.jsonl`; a sender record in `gw-s4-a.jsonl` exactly when the bit is 1; no tenant-A record in log B or tenant-B record in log A.

Comparison with S3 (same server process) is descriptive: different collection time and memory settings.

## Budget

GPU VM time only, about 15 minutes. The VM is terminated after S4 evidence is copied.
