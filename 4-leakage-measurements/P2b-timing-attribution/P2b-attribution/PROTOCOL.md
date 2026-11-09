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

## Amendment v2, 2026-11-08 ~23:30 UTC, before any P2b data

At 22:54 UTC, the previous lead (Codex, in the parent thread) stopped the v1 sequencer. The v1 sources are kept unchanged in `archive/v1`. Its three reasons are accepted, and v2 applies them:

1. **Gate on P2's audit and null, not only its seals.** `p2b.py` and `sequence.py` require `P2-GATE.json`. The lead writes it only after the local P2 controller's holdout audit passes and P2's A/A arm passes its null gate. It records the hashes of those files.
2. **Native audit.** `audit.py` runs after each split on the labhost. A failure stops the sequence. Holdout analysis also refuses to run without a passing `audit-holdout.json`. The audit checks that:
   - each client request joins exactly one gateway row, with the right principal, origin address, method, full target, status and body hash;
   - each forwarded request joins exactly one Artifactory request-log row and one router row, with matching trace IDs, and the router interval lies inside the client interval (±2 ms);
   - blocked requests are never forwarded;
   - in `pergw`, each tenant's requests appear only on its own gateway;
   - in `pad`, every gateway response took at least 12 ms;
   - sender requests appear only in active trials;
   - the schedule is exact, the labels are balanced, the seal matches and cleanup is complete.
3. **No capacity numbers.** Bits-per-trial and bits-per-second "lower bounds" are removed. Results are fixed-decoder accuracy for one sender protocol, with Holm-corrected p-values and median differences.

Nothing else changes: arms, sizes, decoder rule, the 12 ms floor and the predictions.

## Amendment v3, before any P2b data

The takeover reproduced two operational gate defects locally. Edition 2 accepted
approval booleans without verifying the underlying P2 evidence. Its freeze function
also accepted unaudited, undersized calibration after holdout files existed. The
waiting sequencer was stopped before data, and edition 2 remains in `archive/v2`.

Edition 3 checks hashed P2 audit, results, model freeze, four complete 1,000-trial
seals, their data hashes, and cleanup before collection. A boolean alone cannot
authorize the study. The native audit seals all retained split evidence and verifies
complete container topology, live positive and negative route controls, request
grammar, full backend/router targets, trace IDs, response bytes and headers, and
exact coverage. The padded gateway records when headers begin, so the floor check
cannot be satisfied by delaying only after delivery.

Calibration needs four passing current audits and 100 balanced trials per arm.
Freezing refuses existing holdout or decoder files. The freeze pins all sources,
all calibration inputs, seals, and audit metadata; holdout collection and analysis
recheck them. Cleanup failure invalidates completion. Scheduling lead and sender
overrun remain descriptive records, with no trial exclusion.

The four arms, sender action, 8/100/300 sizes, fixed median decoder, 12 ms floor,
predictions, and statistical tests are unchanged. The additional network controls
run and stop before primary trials. These repairs precede all P2b smoke,
calibration, and holdout data.

## Amendment v4, after the first smoke and before calibration

Edition 3 collected and cleaned all 32 smoke trials. Its native auditor then
stopped on a missing public `fixture_etag`. The imported P2 sender-plan function
constructs the conditional-GET description even when the selected recipe is GET.
Private collection state had the actual fixture ETag; public provision omitted
it. The original source manifest, public data, seals and failed sequence log
remain in `archive/v3`. The failed native audit is not counted as passing.

Edition 4 copies that existing public fixture metadata from owned state.
It does not fabricate a value or change the P2 sender-plan source.
Provision now fails if the native metadata is missing. The actual provision
seam is replayed against the retained public fixture, checking that the GET
jobs remain identical. Fresh smoke, calibration and held fixtures use `s2`,
`c2` and `h2` identifiers. Trial assignments, requests, sample sizes, decoder,
12 ms floor, primary tests and null policy are unchanged. Fresh smoke must
pass its complete native audit before any calibration. The independent
arithmetic verifier is rebound prospectively to this edition before those
new data, without changing its calculations or tolerance.
