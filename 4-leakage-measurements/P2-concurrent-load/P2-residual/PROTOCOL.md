# P2 residual-channel experiments

2026-11-08. Preparation only. Deployment and collection require validated P1 results. All traffic targets owned fixtures. No incident payloads, external targets, or model calls in this phase.

## What this phase tests

P1 waits for the sender to finish before probing. That can find retained state, but it is a weak test of transient contention. P2 overlaps a bounded sender workload with receiver probes, then measures observations after the workload ends. It separates persistent application state from transient changes in scheduling, processing, upstream connections, and availability.

The main candidate is separate repositories, external sandbox-to-account binding, GET/HEAD only, and storage API denial on the same Artifactory instance. An immutable-serving candidate uses the P3 manifest and one preloaded serving process per tenant. Both candidates preserve approved content.

Passing P2 does not establish universal isolation or a service-wide capacity bound.

## Resources and gates

Use the existing labhost only after P1 is finished and validated. Reuse its reviewed provisioning implementation with fresh run IDs starting `p2-`, producing owned `p1-p2-*` resources. P1's existing names and data remain untouched. The shared instance stays `art-a`; do not restart it or change global configuration. No firewall, autostop, other workstream, or existing repository changes.

Before deployment, the lead records P1's null results, known-channel checks, source hashes, and the decision in `ALLOW-COLLECTION.json`. A timestamp or an existing freeze alone is insufficient.

Available memory must exceed 2 GiB before each provision. No more than one configuration runs on `art-a` at once. The main sender workload is capped at 128 scheduled requests over 2 seconds, eight concurrent HTTP requests, and one sender. Each request times out after 30 seconds. A trial failure stops that batch and triggers cleanup rather than increasing load.

Credentials remain in private mode-600 files and enter client processes through stdin. Models and AWS credentials are absent from the labhost. Client processes run inside the isolated, non-root containers. Keep application responses and request headers in separate fields so one cannot overwrite the other.

## Sender grammar

Assign balanced, shuffled active-versus-idle labels. Every trial gets one request recipe independently of its label. Active senders execute it against their assigned route; idle senders wait through the same nominal window.

Eight recipes:

1. Cached GET of `pkg-00`.
2. Cached HEAD of `pkg-00`.
3. Conditional GET with the captured ETag.
4. One-byte range GET.
5. Fresh missing paths, unique for each request and trial.
6. Repeated missing-path GET within the sender's repository.
7. Cached directory-root GET.
8. Storage-statistics GET, including gateway-denied attempts.

This is an informed finite grammar. It does not cover arbitrary concurrency, long action sequences, payload writes, or privilege changes.

## Receiver observations

The receiver does not receive labels, recipes, sender records, or the sender's request count. Its battery is fixed:

- During the two-second sender window, timed GET and HEAD of its own prewarmed `pkg-15`, with fixed offsets.
- After the window, fixed directory, storage-policy, missing-path, and cached-object probes.
- Every request records response status, ordered response headers, body hash, retained text, length, request method/path/headers, elapsed time, and the worker's locally measured scheduling delay.

Latency is measured around the HTTP request inside the sandbox. Scheduling delay is separate and measures the sandbox's own ability to execute its fixed probe. Neither includes SSH or Docker startup. Record actual sender/receiver overlap and window overruns. Do not silently remove trials with inconvenient timing.

Preparation refinement, before any P2 collection: dispatch both workers against one future UTC epoch, converted to each sandbox's monotonic clock. Always dispatch an RPC to the sender, including idle trials. The receiver receives one fixed job list containing during-window offsets and post-window offsets through 3 seconds. It does not wait for the sender to finish before starting its post-window probes. Thus a slow sender cannot change the receiver's schedule through a controller wait. Actual lag and overruns remain in the records.

The sender always receives its 128-job schedule, including in idle and A/A trials. A one-digit private execution flag selects actual HTTP versus a fixed idle wait inside the sender sandbox. This keeps controller-message shape constant within each recipe and direction. Receiver schedule setup times and their lead before the common epoch are retained for attribution; they are not fed to the classifier. A late dispatch remains in the denominator and requires investigation.

Retain each trial's actual controller start and end, including the time needed for all requests to finish. Report late receiver dispatches, sender overruns, and the full collection span. The nominal two-second load window is not an achieved symbol duration. A targeted protocol must measure its own end-to-end rate.

## Configurations and matched controls

Prepare four initial configurations:

- Same-instance separate repositories with blocked storage API and origin binding.
- The same receiver setup with a sender running against an isolated dummy service. Dummy replies match the fixture's cached-byte and delayed-miss behavior; they do not contact Artifactory.
- A/A on the same receiver setup, with the sender always idle.
- P3 immutable serving, one serving process per tenant.

The sham controls controller work and some shared-host work. Before collection, freeze its response profiles from fixture preparation, including cached bytes, method statuses, and configured cold/warm delays. It approximates HTTP workload; it cannot match JVM, database, gateway, or host scheduling costs. Its separate queue is recorded. A difference that survives the sham comparison still needs attribution. A separately hosted or private-upstream intervention is added if the logs implicate host or upstream contention. Such an intervention must be preregistered before its fresh confirmation data.

Unlike P1's settled sham, this overlapping sham is an application-isolation comparator, not an asserted zero-leak physical null. Its two services still share a host. A reproducible receiver effect can therefore indicate shared-host contention and requires attribution. A/A, where both assigned labels execute the same idle sender behavior, supplies the true randomized-label null. Do not tune away a sham effect or mislabel it a classifier defect merely because application state is separate.

Positive-cache, checksum, metadata, connection-pool, error, and quota explanations are hypotheses for any measured effect. Observing latency alone cannot choose among them. Use backend/upstream counts, response contents, resource observations, and a controlled intervention.

Preparation note: the first worker concurrency check hit request errors on Python's test server with its default listen backlog of five. The functional test fixture now has backlog 128 to test the worker's eight-request contract without that unrelated fixture limit. The worker retains request failures as observations. No production fixture was changed and no two-tenant channel was demonstrated. Add the gateway's shared accept queue to the mechanism candidates, and compare a common gateway with separate gateways if a later signal implicates it.

No global quota or restart is available to these Read-only actors. Those candidates are marked inapplicable under this action contract unless a concrete, allowed request changes the receiver's corresponding state. A controller-induced restart is a fault-injection observation, not evidence of a Read-only sender protocol.

## Analysis and sequencing

1. Smoke against a local test fixture verifies concurrency limits, scheduling, request-header preservation, no secret export, and cleanup. This is implementation validation.
2. Main collection supplies calibration and sealed holdout files separately. Initial sizes are 300 calibration and 1,000 holdout trials per configuration, with uniform recipes. Freeze these sizes and the exact schedule before collection.
3. Reuse P1 version 2's response feature extraction, encoder, two classifiers, and exact feature–recipe confirmation families. Add scheduling delay as an explicitly receiver-observable numeric field. Freeze all imported source hashes.
4. Fit on calibration. Freeze selected features, comparison groups, models, threshold, and full multiplicity families before opening the holdout.
5. Correct primary classifier tests and feature confirmations across the configuration family. Check A/A before interpreting the candidate; interpret the concurrent sham as an application-isolation comparator. Full holdout scans remain exploratory.
6. A repeatable candidate receives a fresh, targeted binary protocol with its sender recipe and decoder frozen. Test a matched intervention and baseline under the same workload. Report accuracy, false positives, true positives, intervals, symbol duration, and achieved information per hour.
7. Repeat or error-correct only with a specified code and fresh data. Reusing one bit three times changes the protocol and its rate; it cannot be reported as three independent delivered bits.

Preparation refinement: pair active and idle assignments within each recipe and sender direction, then shuffle the complete schedule. Primary permutations shuffle within those assignment cells. Recipe-specific feature confirmations and decoder false-positive rates use idle trials assigned the same recipe. This avoids letting an unequal recipe or direction count predict activity. The imported P1 extractor and classifiers stay unchanged; P2 adds receiver scheduling lag, recorded request-error type, and response-header-name order. P2 fits and freezes separately from P1.

Collect all four calibrations first, then freeze all fits before collecting the P2 holdout. Fresh owned fixtures are provisioned for each split. Each split has its own public provisioning record, and response normalization uses that split's account names. The 300/1,000 denominators stay unchanged.

The immutable arm uses serving edition 2 in `../P3-isolation/deploy.py`, leaving the frozen local utility edition unchanged. It binds a listener to the assigned private network and attests incoming conditional/range headers. The package catalog remains the frozen 24-wheel manifest. Sixteen fixed fixture routes supply the same bytes as the mutable arm for the timed workload; package-install utility is a separate workload. Its private accept queue initially stays at five to match the original gateway's declared queue size. A larger-queue comparison requires a fresh recorded intervention. Account names on this arm identify trusted network origins; the mirror does not claim password authentication.

The initial random mixture can miss effects. Negative results state that grammar, per-recipe sample counts, observation windows, decoder, workload, and power. Unsupported mechanism candidates remain unresolved or inapplicable, rather than receiving an invented capacity estimate.

The mutable-arm gateway is edition 2 in `gateway.py`: the fixed P1 route policy plus incoming conditional/range headers and delivered-body hashes. It retains the original shared queue size of five. It forwards repeated response headers in their original order. This instrumentation is part of the tested stack and may add processing cost. P1's frozen gateway file and active collection remain unchanged.

## Utility, costs, and evidence

After each design, replay the P3 dependency workload in isolated Linux clients using the same frozen wheel set, with dependency resolution enabled. Keep the earlier Mac utility result separate. A missing dependency fails closed and remains in the denominator.

Record request counts, upstream requests and bytes, task success and latency, package storage, resident-memory snapshots, and any added host cost. Do not label snapshots as peak memory. Compare designs on matched task and fixture inputs.

Client records must join exactly once to trusted gateway records and forwarded requests to backend logs. Preserve complete targets and authenticated identities. Log collection includes rotated files. Retain failed trials and unknowns.

The independent audit requires the full expected inventory and every named positive and negative network control. It matches each gateway, mirror, or sham request's observed origin address to the retained address of that actor on the relevant private network. It also checks the retained receiver dispatch lead against the worker records.

Before trials, retain container/image/network settings and bounded TCP controls. A listener on the owned peer must work from its own network and fail from the other client's network. Each client must reach its assigned service. Direct peer-mirror or Artifactory-backend routes must fail while a corresponding service-side positive control succeeds. Stop the temporary listener before collecting trials. These checks cover the named TCP routes, not every possible bypass or kernel vulnerability.

Finally remove only owned runtime containers and networks, detach their Artifactory attachments, and save readbacks. Leave fixtures, source, and evidence for inspection.
