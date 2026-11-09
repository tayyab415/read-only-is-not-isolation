# P2b: attribution and mitigation of the concurrent-load timing candidate

Written 2026-11-08 ~23:10 UTC, before any P2 holdout file was opened. Runs on the existing labhost after P2's holdout collection, audit and cleanup finish. Owned fixtures only. No model calls.

## Why

P2's calibration shows the separate-repository receiver can tell when the sender is active. Cross-validated accuracy is 0.94 for separate, 0.70 for immutable, 0.58 for sham and 0.51 for A/A. The selected features are almost all receiver timed-probe latencies during the sender window. The effect runs the "wrong" way: the receiver's own cached GET/HEAD is faster while the sender loads the shared service (calibration medians 3.34 ms active vs 3.83 ms idle). It is absent for the gateway-blocked `stats` recipe and in sham and A/A. This is calibration-only, exploratory evidence. P2's own frozen holdout tests decide whether it replicates.

The NEXT-CONTINUE rule applies: a timing candidate needs a fresh sender protocol, a frozen decoder and a controlled intervention before any claim about cause or rate. P2b is that step.

## Questions

1. **Replication:** With one fixed sender action and a one-number decoder, does the effect transfer information on fresh fixtures?
2. **Attribution:** Is the shared gateway the cause? We wrote the gateway, and it is CPU-limited to 0.5. Or is it the shared Artifactory/host path?
3. **Mitigation:** Does holding every gateway response to a fixed minimum latency remove it, and at what latency cost?

## Arms

All arms use P1/P2 provisioning: separate repositories, storage API blocked at the gateway, origin binding, GET/HEAD only, the same art-a instance, the same client limits.

| Arm | Gateway topology | Sender |
| --- | --- | --- |
| `shared` | One P2 gateway (edition 2, queue 5) shared by both clients | active on label 1 |
| `pergw` | One identical gateway per client. Each is on its own client network plus the service network and binds only its own client. | active on label 1 |
| `pad` | One shared gateway. Every response is held until 12 ms after the request arrived; nothing is sent earlier. | active on label 1 |
| `aa` | As `shared` | never active (null) |

The 12 ms floor is set from P2 calibration, before P2b data. Receiver timed probes are about 3-4 ms, and the sender's 8-concurrent load should not push a cached response past 12 ms. If the floor is too low, padded responses still vary. That outcome is reported, not tuned.

`pergw` doubles gateway CPU in total. That is the design difference being tested: per-tenant gateway processes. It is not a pure attribution to a single code path.

## Trial

The sender's command list is the same in every trial: P2's `get` recipe, 128 GETs of its own cached `pkg-00` at concurrency 8 over 2 s. A private execution flag inside the sender decides whether the requests are sent (label 1) or skipped (label 0, and always in `aa`). Both workers are dispatched against one future epoch. The receiver runs P2's fixed job list: 15 timed GET/HEAD probes of its own cached `pkg-15` during the window, then six post-window probes. Labels are balanced within sender direction and shuffled with a fixed seed.

## Decoder (frozen on calibration)

Statistic S is the median `elapsed_ms` of the receiver's 15 timed probes. Calibration sets the threshold at the midpoint of the two class medians of S, and sets the direction from their order. The decoder is frozen (`DECODERS-FROZEN.json`, with hashes) before any holdout trial is collected. The holdout uses fresh fixtures.

## Sizes and tests

- Smoke: 8 trials per arm.
- Calibration: 100 trials per arm (50/50).
- Holdout: 300 trials per arm (150/150), on fresh fixtures.

Primary test, per arm: exact one-sided binomial test of holdout accuracy against 0.5. Holm correction across the four arms.

Achieved rates are reported as lower bounds for this protocol:
- bits per trial, 1 − H₂(error);
- bits per second, from the mean measured trial period.

Secondary comparisons:
- `pergw` vs `shared` accuracy and `pad` vs `shared` accuracy, by Fisher exact test;
- the latency cost of `pad`, as median and p95 of receiver probe latency against `shared`.

With 300 holdout trials, a 3-SD threshold needs 176/300 correct. Power is 80% at about 62% true accuracy. Smaller effects are reported as sensitivity, not as absence.

## What would count

| Outcome | Reading |
| --- | --- |
| `shared` significant, `pergw` near 0.5 | Our shared gateway caused the coupling, and per-tenant gateways remove it in this test. |
| `shared` and `pergw` both significant | Coupling through Artifactory or the host path. Per-tenant gateways are not enough. |
| `pad` near 0.5 with `shared` significant | A fixed response floor hides this signal at the measured latency cost. This says nothing about other probe types. |
| `aa` significant | The pipeline is broken. Stop and report. |

## Evidence

- Raw receiver and sender records per trial, with sealed SHA-256 per split.
- Gateway logs, including both gateways in `pergw`.
- The Artifactory/router log lines carrying the run marker.
- A boundary readback and a cleanup readback.

Failures stay in the denominators.
