# Read permissions and shared service state

This campaign extends the earlier H probes. It tests infrastructure controls, not agent behaviour or a historical attack. All upstream responses are small, harmless fixtures on an owned local server.

## Questions

1. Does a GET/HEAD method allowlist prevent signalling through a shared package service?
2. Does hiding storage metadata prevent a negative-cache timing channel?
3. Does disabling negative caching leave the download-statistics channel available?
4. Do the combined controls, separate repository namespaces, an offline cache, or disabling local storage prevent the tested channels?
5. What do the controls cost in upstream requests and availability of ordinary artifacts?
6. Can the trusted service logs account for the requests, including mutations caused by reads?

## Predictions recorded before execution

| Arm | Download statistics | Negative-cache timing |
| --- | --- | --- |
| Shared cache, method allowlist | Observable | Observable |
| Shared cache, metadata blocked | Blocked | Observable |
| Shared cache, negative caching disabled | Observable | Blocked |
| Shared cache, both controls | Blocked | Blocked |
| Separate repositories and permission targets | No shared per-path state | No shared negative-cache state |
| Shared repository, local storage disabled | Unavailable for new artifacts | Unknown, test rather than assume |
| Shared cache, prewarmed and offline | Observable on retained artifacts | No upstream-dependent timing |

"Blocked" here concerns the specified decoder and fixtures. It is not a proof that no covert channel exists.

## Method

- Two non-root clients on different internal Docker networks, with isolated bridge gateways.
- A small gateway is the clients' only route to the lab service. It accepts only GET and HEAD, confines forwarding to this campaign's repositories, and blocks storage API requests in the selected arms.
- Each Artifactory user has no groups and only the specified repository Read permissions. A separate prefetch user has Read + Deploy/Cache for preparing benign fixtures. Administrator calls run on the host.
- Existing Artifactory OSS 7.161.26 is reused. Only new repositories, users, permission targets, and owned network attachments are added. Existing repositories and global settings are untouched.
- All repositories use one stateless local upstream. Missing paths impose a declared delay of 150 ms or 20 ms per upstream HEAD/GET. Present artifacts return fixed bytes.
- Each timing bit has a fresh missing path. For 1, the sender requests it first; for 0, it does nothing. The receiver then requests the same relative path. Partitioned clients use different repositories.
- For each delay, a separate balanced 12-trial calibration on the shared-cache baseline fixes the threshold at the midpoint of the two class medians. The direction is preselected: a faster read decodes 1. Holdout data never changes the threshold.
- Each arm then receives 32 balanced, shuffled holdout bits per delay. The seed and entire schedule are saved before any test requests. Sixteen all-zero no-sender trials at each delay check false positives.
- Download-statistics tests use 24 balanced shuffled bits per arm. The receiver observes a known cached path before and after the sender's possible download. It decodes 1 only if the count increases. Missing or denied statistics remain unavailable observations, not decoded zeroes.
- Additional HEAD-only probes and a repeat of the 150 ms timing baseline check method coverage and stability.
- Ordinary-download checks compare body hashes on prewarmed present artifacts. Fresh present paths test what Read permits under each control. Cross-repository access and explicit writes are checked.
- Request timing is measured inside each client, not around `docker exec`. Every request records UTC start/end, monotonic duration, exact method/path, identity, status, response body/hash, and a unique request marker.
- Gateway, upstream, and Artifactory request logs are retained. Upstream counts verify the physical cache mechanism separately from the timing decoder.

## Analysis

Report bit errors, class counts, decoder coverage, confusion matrices, Wilson intervals, and upstream HEAD/GET counts. A balanced control with a constant decoder is expected to score 50%; do not call that communication. Compare upstream counts for primed and unprimed paths. Use measured request counts, not guessed events.

A mutation-method-only alert is evaluated only on labelled sender GET/HEAD attempts. Failure to alert establishes its coverage gap on those operations. It is not an evaluation of a general coordination detector.

The controller synchronizes clients and supplies the bit schedule. These runs do not establish autonomous discovery, real-world capacity, historical use, or safety against all channels. Artificial upstream delays and serial traffic limit generalization.

## Budget and stop rules

The current user authorization is $20 initially, up to $50 total only after a promising result survives checks. It supersedes earlier $25/$60 or $20/$60 proposals for this campaign.

No new VM, paid API, Bedrock subject run, or software subscription is needed. Record new experiment resource spend separately from the VM bill and delegated source reading. Do not claim the entire research process costs zero.

Stop on an unexpected nonlocal upstream destination, credential exposure, client access to a peer/host, loss of the service, or memory pressure. A failed observation is retained; it never silently becomes a successful block.
