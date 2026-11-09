# P8: host CPU counters with private application state

Prepared 2026-11-08 UTC. This is a prospective experiment, not a result. Use the existing labhost after P2 collection finishes. No model calls, incident reproduction, new VMs, shared-service changes, or external targets.

## Question and scope

Can a scripted shell-enabled container infer another container's CPU activity from aggregate `/proc/stat` counters while their files, networks, serving processes, and cgroup views are private? Does replacing that one file remove the input while preserving private file writes and approved HTTP reads?

The primary-source review is `../../reports/P8-telemetry-sources.md`. Linux's aggregate CPU counters sum kernel-wide accounting. Cgroup v2 accounting has a different scope, which must be established from the actual mount and namespace. Read-only paths do not conceal their contents. Moby's pinned defaults omit `/proc/stat` from masked paths. LXCFS already provides cgroup-aware telemetry and is prior art. P8 does not implement LXCFS or claim this mechanism is new.

This observer has a fixed operating-system reader beyond P2's HTTP-only interface. A P8 result cannot explain a P2 timing effect or show what an HTTP-only model could observe. The sender runs a fixed CPU workload rather than HTTP requests during the measurement window. This separates the counter mechanism from mutable application state. The trusted host sampler remains outside the observer's inputs.

## Fixed deployment and intervention

Four configurations:

- `native`: Bob reads real `/proc/stat`; Alice runs the CPU workload for label one and idles for label zero.
- `masked`: identical behavior with one owned, static regular file bound read-only over Bob's `/proc/stat`.
- `aa-native`: native Bob view; Alice idles under both randomized labels.
- `aa-masked`: masked Bob view; Alice idles under both randomized labels.

Alice and Bob each have a private immutable mirror and an isolated internal network. Both clients explicitly use private cgroup namespaces, run as UID/GID 20001, drop all capabilities, use no-new-privileges, have read-only roots, 128 MiB memory with no additional swap, a 0.5 CPU quota, 48 PIDs, and a writable 16 MiB private tmpfs. No AWS credentials enter clients. No host mount enters Alice. Bob's only bind mount in masked configurations is the specified immutable file. Mirrors retain the previously frozen public source and catalog.

Create clients with the final cgroup namespace and mask settings before configuring each mirror's trusted origin. Verify final boundaries and live TCP controls. Record actual Docker, kernel, image, namespace, cgroup mount and quota identities. Require cgroup v2 and an effective `cpu.max` ratio of 0.5. Different namespaces are evidence about those namespaces, not every shared resource.

The static file contains one aggregate row and one row per host CPU, all with fixed CPU counters. Its exact bytes and SHA-256 remain in the public evidence. Record the actual bind source, destination and read-only status; read the entire file in Bob at startup and each trial. A failed bind or changing content fails the mask audit. Do not substitute a simulated reader or zero out live observations in analysis.

This intervention removes one counter input. Other procfs/sysfs files, inherited descriptors, alternative mounts, observer scheduling, and request timing are outside the claimed closure. `/proc/stat` includes fields with different namespace behavior, including `btime`; the study only uses the aggregate CPU row.

## Bounded trial

Both actors receive an RPC on every trial. The sender receives one private, one-digit execution flag; Bob's RPC contains no label, flag, recipe, sender record, or host sample. Both start independently against one future UTC epoch, converted to local monotonic time. Bob never waits for sender completion.

At startup, each actor writes and reads back the same fixed 1 KiB file on its own tmpfs. During every trial each actor repeats that file write/read and performs one GET before the measurement epoch and one at epoch plus 2.5 seconds. The URL is its assigned mirror's `/fixture/pkg-00.bin`. All successful bodies must match the frozen 31-byte fixture. The four GETs and two file tasks happen for every label and configuration. These are narrow utility checks; they do not replace P3's Linux dependency-resolution workload.

Alice starts at epoch plus 0.2 seconds. For two seconds, active Alice hashes a fixed 64 KiB byte array in one process. Idle Alice sleeps through the same window. Record planned and actual times, process CPU seconds, cgroup usage before/after, and hash iterations. This is a bounded 0.5-CPU workload, not arbitrary shell or a model-selected protocol.

Bob reads `/proc/stat` and its own `/sys/fs/cgroup/cpu.stat` at 22 fixed offsets, 0.1 to 2.2 seconds in 0.1-second steps. Preserve raw aggregate lines, full-file hashes, cgroup records, read bracketing timestamps and scheduling lag. A trusted host reader samples its own native `/proc/stat` at the same offsets. Host data attest accounting scope and intervention behavior; the decoder does not receive them.

Read failures, late dispatches, late samples, overruns and failed utility tasks remain in the denominator. An RPC failure, unreadable accounting file, HTTP failure, or failed utility readback stops the batch after retaining the trial and attempts owned cleanup. Late but complete samples are retained without adaptive retries or exclusions.

## Schedule, freezing and analysis

Preparation tests precede a fresh native smoke of four trials per configuration. Smoke data never enter the scientific analysis. Main calibration has 24 trials per configuration; holdout has 64. Each consecutive block of four trials contains two active labels and two idle labels, independently shuffled with fixed seed 2026110808, explicit split/configuration offsets, and fresh resources per split. All four calibrations are complete and audited before any holdout collection or access.

The single primary feature is aggregate busy fraction over Bob's first-to-last sample:

`total = user + nice + system + idle + iowait + irq + softirq + steal`

`busy_fraction = delta(total - idle - iowait) / delta(total)`

Guest counters are excluded to avoid double counting. Preserve decreasing iowait values. A constant static view has zero total delta, a feature value of zero, and an explicit unavailable-live-counter indicator. Negative total or busy deltas fail the accounting audit rather than being clipped. If the native host supplies such a case, retain it and declare that this preregistered feature cannot be evaluated.

For each configuration separately, calibration chooses the best threshold among all midpoints and constant predictions, and either direction. Resolve ties by candidate enumeration order. Freeze source hashes, every calibration input, all four thresholds/directions and the family before fresh holdout collection. The decoder uses no file hash, host data, cgroup data, request timing, scheduling lag, marker, trial index, sender record, or ground-truth field.

Primary tests use 9,999 blocked randomizations plus the observed assignment. Shuffle labels within the original balanced four-trial blocks, keeping predictions fixed. Correct the four configuration accuracy tests with Holm at family alpha 0.05. Both randomized-label A/A controls must pass before attributing an active-arm accuracy effect. Passing means neither control has a corrected p-value below 0.05; it does not establish a population false-positive bound.

Report all 64 held trials per configuration, accuracy, confusion counts, true/false-positive rates, 99% Clopper-Pearson intervals under an explicit IID Bernoulli assumption, blocked corrected p-values, effect sizes, and actual wall-clock trial duration. The small denominator limits accuracy estimates. No capacity, information-rate or autonomous-agent claim follows from this decoder.

Own-cgroup usage, sampling lag, HTTP timing, file hashes, host-counter matches, active sender CPU use and differences between configurations are descriptive mechanism evidence. They are not additional uncorrected discovery tests. A native positive plus verified host accounting and a static-mask loss of this input supports the named counter mechanism. It does not establish general timing isolation.

## Independent audit and gates

Source/import hashes, actual P1 validation, complete P2 collection/cleanup, and no running P2 resources gate the native experiment. Acquire the same P2 collection lock throughout each configuration/split; do not overlap any other VM experiment or native build. No automatic launch while P2 runs.

The audit checks exact schedules and denominators, seals, source identities, final namespace/mount/limit readbacks, stopped TCP controls, independently timed sender execution, receiver RPC separation, full sample inventories, constant mask bytes, cgroup scope, and host/container counter agreement within a timestamp-derived quantization tolerance.

It joins each actor's two HTTP requests exactly once to its assigned mirror's response and wire records, including actual origin, trusted identity, marker, path, method, interval, status, body hash and request headers. Four marked requests per trial imply zero label-dependent HTTP request overhead. Preserve all unrelated preparation records rather than using them as trial observations.

All owned containers and networks must be absent after each split. Keep sources, public static files, configurations and evidence. Never export credentials. A failed scientific gate is retained and stops dependent interpretation; it is not fixed by tuning the decoder.

## Cost and interpretation

The experiment uses existing reserved VM time and makes no paid provider calls. It is capped at four smoke configurations plus eight main configurations, one sender process, 0.5 CPU per actor, two-second sender windows, and four approved GETs per trial. Total planned trials are 16 smoke and 352 main; total trial GETs are 1,472. Preparation TCP checks and readiness GETs are separately counted.

The architectural recommendation, if the mechanism confirms, is to treat host telemetry as part of the shell-enabled sandbox's observation contract. Preserve private file storage. Prefer verified cgroup-scoped accounting for ordinary diagnostics, and evaluate LXCFS or explicit masking with their own fail-closed checks. Shared-host timing protection requires another experiment and potentially stronger resource separation.
