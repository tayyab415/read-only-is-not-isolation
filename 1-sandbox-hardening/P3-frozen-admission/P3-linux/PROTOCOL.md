# Matched Linux dependency workload

Prospective protocol, 2026-11-08. Preparation only. No deployment or utility outcome exists yet. Keep the earlier Mac utility edition and manifest unchanged.

## Question

Can both the separate-repository service and private immutable serving complete the same dependency-resolving tasks in confined Linux clients?

Use the frozen P3 manifest of 24 universal wheels and the fourteen pinned roots. Each arm runs one fresh install of every root for each of two tenants, for 28 tasks per arm and 56 total. The root, dependency, index, byte-hash, Python-image, client-limit, and function-check inputs are identical. Keep failures in all 56 scheduled tasks.

The comparison has a common private index adapter in front of each tenant. It serves only the frozen HTML index. Wheel requests pass through it to the candidate backend. For the mutable arm, the existing gateway and Artifactory serve prewarmed wheel bytes from the assigned repository. For the immutable arm, the assigned private mirror serves the same bytes. The adapter must not supply wheel bytes from its locally loaded catalog.

This evaluates the declared payload-serving routes with a common index adapter. It is not a full native PyPI registry benchmark or an exact performance comparison of the unmodified P2 request route. Disclose the added adapter and its origin mapping. A fresh utility result cannot retroactively validate P2 timing.

## Runtime and admission

Deploy only after P1's real evidence gate passes. Hold P2's common collection lock throughout this workload. Do not run utility traffic during a leakage collection, and do not change frozen P1 or P2 sources.

Use fresh owned run IDs on the existing lab host or the already approved lab2. Clients retain the non-root, read-only-root, capability, CPU, and network restrictions. Give both utility arms the same 256 MiB client memory and swap limit, 128 MiB private `/tmp`, 0.5 CPU, and 48-process limit. The larger private workspace is a prospective utility provision, not a change to P2's timing clients. Pip can temporarily hold unpacked files and their final copies together. Record actual Python/pip versions, image IDs, network boundaries, and source hashes. No further VM launch or paid model call belongs in this workload.

Trusted setup prewarms all 24 wheels in each mutable namespace and checks the retrieved SHA-256 and length before untrusted client tasks begin. Its upstream is a private serving process containing only the already admitted catalog. No run-time external package retrieval, refresh, or admission is permitted.

The trusted prefetch principal fills Artifactory through its existing host endpoint before the client route is bound. These setup reads are outside the untrusted gateway and have separate request/router and upstream evidence.

An adapter accepts only its externally bound network origins and its assigned task paths. It injects its scoped backend credential on the mutable route. Client tasks receive no password. Configuration and credentials enter the adapter through stdin, with no credential-bearing argv or environment. Preserve the recorded actor mapping and verify the new route before collection. Only adapter origins enter the backend HTTP allowlist. Retain a direct uncredentialed HTTP rejection from each client, plus the specified live TCP controls.

## Task schedule and success

Freeze the two arm orders and the same shuffled 28-task schedule before deployment. Use the earlier workload's fourteen package-function checks, preserving their exact source hash.

Each task gets a private temporary install directory and a fresh Python process. Run pip with the assigned index, cache disabled, installed packages ignored, version checks disabled, binary wheels only, and compilation disabled. Dependency resolution stays enabled. Remove inherited pip variables and record the full pip install report.

Success requires pip exit zero, the exact pinned root version, imports from the fresh target directory, the offline function check, and every reported download URL/digest/version inside the assigned frozen catalog. A successful pip exit by itself is insufficient.

A task-scoped URL prefix lets the service logs join an install to all of its index and wheel requests. The adapter strips the prefix only when forwarding an approved wheel. Its trusted request marker, generated outside the client, joins the backend records. The task prefix is part of this utility interface, not a covert-channel observation.

Each task times out after 120 seconds of pip work and 30 seconds of function checks. Retain timeout and failure records, then continue the remaining independent tasks unless the boundary, backend integrity, or cleanup fails. Those infrastructure failures stop the arm and leave its remaining denominator incomplete.

## Evidence and costs

Retain task reports, pip/function outputs, task elapsed time, installed bytes, adapter incoming requests, forwarded full targets, returned body hashes, backend/mirror logs, setup fetches, upstream bytes and requests, and cleanup readbacks.

Report cold install tasks against prewarmed service content. Do not call the backend a cold cache. Setup fills count separately from run-time upstream traffic. Capture process or cgroup memory observations before and after tasks, identify which measurement they are, and do not describe snapshots as peak memory.

Compare success, matched task latency, request count, payload bytes, admitted storage, run-time upstream work, and measured resource observations. Shared Artifactory's pre-existing memory is not attributable solely to these 24 wheels. Private servers' preloaded copies and additional adapter processes have explicit costs.

This workload does not test native extensions, source distributions, build dependencies, extras, unrestricted package coverage, dynamic dependency admission, or timing isolation. Every claimed result must name the Python/platform, fixed catalog, adapter edition, and complete denominator.

## Edition 3 host amendment, before Linux data

The user approved lab2 so follow-up studies can proceed while P2 and P2b occupy
the lab host. The matched Linux utility comparison is a prerequisite for those
studies and will run on that same approved lab2. Its m6a.xlarge host uses the
same pinned Artifactory image, PostgreSQL version, original provisioner sources,
and admitted catalog. Lab2's P7 build has completed before utility collection.
The common collection lock and all native boundary and audit gates still apply.

Edition 2 remains in `archive/v2`. The executable sources, arm order, task
schedule, workload, limits, and success predicates are unchanged. Report this
host explicitly. This utility result will not establish a timing result on the
lab host or a general result across hosts. `../VM2/BUDGET-RESERVATION.json` reserves
lab2 separately without resetting the subject ledger.
