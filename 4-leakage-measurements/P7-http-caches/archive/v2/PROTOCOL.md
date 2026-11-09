# Two additional HTTP cache implementations

Prospective preparation. No binary has been built or deployed and no P7 trial has run. The primary-source review is `../../reports/P7-cache-sources.md`. Retrieved release archives match that review's SHA-256 values.

## Question and scope

Does the controlled cross-tenant 404 cache observation recur in NGINX and Squid, and do trusted key partitioning, separate instances, and disabled caching remove its application-state cause while preserving successful object reads?

Use NGINX 1.30.6 and Squid 7.8, built from the verified archives. Squid must expose `--enable-http-violations` and accept an explicit 60-second `negative_ttl`. Its default zero TTL is not this treatment. Reject the cohort if the binary, configuration parse, or two-read canary fails. Preserve build and failed-canary evidence. Do not substitute different response headers to force a pass.

These are generic HTTP caches, not Artifactory package services. The configured Squid negative-cache feature is nondefault. A successful comparison adds implementation evidence for a known mechanism; it does not establish a new architectural invention or historical attribution.

## Arms

For each product and 20/150 ms missing-object delay, test shared cache, trusted tenant key, private cache instances, and cache disabled. Keep the origin bytes, status, and headers identical across arms.

Each client has its own confined network. The cache connects the allowed client network or networks to a private origin network. Clients cannot reach the origin or a live peer listener directly. A read-only root, non-root user, capability drop, private cache directory, CPU/memory limits, and exact container image/configuration hashes are retained.

NGINX maps the externally observed client address to a fixed actor before cache access. Unknown addresses receive 403. The tenant-keyed arm adds that trusted actor to the key. Private instances have distinct memory zones, processes, and cache directories.

Squid admits only the two trusted client addresses. Its StoreID helper receives Squid's observed client address through `%>a`, never a client-supplied identity header. It adds that actor to a hash of the exact absolute URL. Record every helper input, mapping, and output. Missing mappings, helper failures, unexpected fallback, or a shared key in the keyed arm stop the cohort. Private instances have separate memory and disk stores and no parent cache.

Disable collapsing, stale fallback, and background refresh. Cache-disabled arms bypass both read and fill. Preserve the parsed effective configuration and version/build flags. A tenant-key test is meaningful only if ordinary reads still succeed and its key ledger matches the trusted address readback.

## Origin and observations

Missing objects return J's exact `Missing harmless fixture\n` body and 404, with no freshness header. They sleep for the selected fixed miss delay. Successful objects return fixed harmless bytes and an ETag. The origin ignores conditional and range headers, as J's origin does. Keep cache rewrites in raw evidence instead of assuming identical wire behavior across products.

Each trial has a fresh bounded URL. An active sender completes one GET fill before the receiver is released; an idle sender executes the same private worker command without an HTTP request. Sender and receiver are different actors. Receiver GET status/body bytes must be identical across labels. Retain full response headers and native hit labels separately.

The decoder consumes only receiver elapsed time. Date, Age, cache labels, and native log fields are not inputs. This is a declared timing-only decoder, not a claim that those fields are unobservable. Time to completed response headers and to completed body are retained. The header time is not a socket-level first-byte measurement. New connections, explicit no-retry behavior, fixed work, and sender-fill completion are recorded.

## Schedule and freeze

Collect 12 balanced shared-cache calibration trials per product/delay. Freeze one threshold decoder per product/delay, its source, calibration hashes, and exact future schedule before opening main holdouts. Do not learn a new threshold from a defense or holdout arm.

Then run 32 balanced fresh holdout trials in each of the four arms and 16 randomized-label no-sender trials per product/delay. This is 624 primary trials overall. Randomize arm order and actor direction with a published seed. Preserve failures and all scheduled denominators. Do not rerun an unfavorable trial or silently replace a failed cohort.

Cap controller-issued HTTP requests at 1,200 and total client-plus-origin HTTP requests at 2,400. The source review's proposed 1,400-request limit did not distinguish these layers; this prospective protocol does. The 624 primary trials require 904 controller requests. Reserve two total-request slots before each cache request, including the possible single origin lookup. Direct origin controls reserve one. A process-locked ledger reserves before dispatch and retains interrupted reservations. Both products disable forwarding retries; more than one origin request for a marked client request stops the cohort. No client retry occurs without a separate counted reservation.

Canaries cover configured 404 reuse, unchanged successful bytes, and bounded HEAD/conditional/range behavior. They report observed product semantics and do not impose an unsupported uniform result. All canary traffic stays outside the primary trial URLs and decoder fitting.

## Causal checks and reporting

Join each native cache access record to its client address, trusted actor/key, method, full target, marker, status, and request window. Join every origin request to its cache request marker and exact response bytes. A sender-dependent skipped receiver lookup in a partitioned arm falsifies that arm's application-state isolation, even if its timing decoder misses the effect.

Report both the classifier and deterministic cache cause. In shared active trials, an observed cache hit requires the sender's completed fill, the receiver's identical resource key, and no receiver origin request. In idle shared trials and every partitioned/private/disabled trial, retain the expected receiver origin lookup. TTL expiry, eviction, policy bypass, or missing joins make the declared comparison invalid rather than proving a defense.

Report balanced accuracy, errors, Wilson intervals, no-sender false positives, successful reads, HTTP request counts, payload bytes, upstream work, and cache/process overhead. Use exact label randomization preserving the fixed balance within receiver direction. Correct the four shared positive-control tests across product and delay with Holm's method. Wilson intervals are descriptive binomial approximations, not intervals across independent host deployments. Treat defended-arm timing as exploratory; a positive association needs a fresh targeted experiment and a physical intervention before causal attribution.

Cache partitioning does not remove shared-host timing. The private-instance arm still shares the VM, kernel, filesystem cache, and CPU scheduler. A chance-like result with this one decoder does not establish zero channel capacity.

Build and run only in an unused collection window after P1 passes its evidence gate. Hold the common P2 collection lock. Do not build caches or run this workload during another leakage collection. Use only fresh owned P7 resources and the existing labhost, with no Artifactory mutation, new VM, or model call.
