# P3 mirror preparation and utility protocol

2026-11-08. Written before package retrieval or utility outcomes. This preparation can run while P1 collects. It does not count as validation of sandbox isolation. Leakage experiments wait for the P1 validation gate.

## Question

Can a fixed, verified package set support ordinary dependency resolution without a mutable pull-through service in the agent request path?

The candidate design has a trusted admission phase followed by immutable serving. A trusted controller resolves approved package requirements, downloads wheels, verifies each wheel against the publisher's SHA-256, builds a static Python simple index, and freezes the manifest. Each tenant gets a separate serving process. The processes can read the same content files, but do not share application counters, negative-cache entries, queues, or request-visible logs.

This does not remove shared CPU, memory, disk, kernel caches, or network contention. Separate processes alone cannot establish timing isolation. The prototype has loopback listeners for local utility work. A later deployment must bind each listener to its assigned sandbox network and independently test bypass routes.

## Utility workload

Fourteen pinned root packages, two independent fresh installs each:

- requests 2.32.3
- rich 13.9.4
- python-dateutil 2.9.0.post0
- networkx 3.4.2
- click 8.1.8
- packaging 24.2
- attrs 24.3.0
- typing_extensions 4.12.2
- pytest 8.3.5
- beautifulsoup4 4.13.3
- more-itertools 10.6.0
- toolz 1.0.0
- tenacity 9.0.0
- pluggy 1.5.0

Resolve transitives for CPython 3.11 using universal binary wheels only. Record the resolver command, Python and pip versions, publisher JSON hashes, wheel URLs and hashes. No source builds. Dependencies are pinned by the frozen manifest after admission; both clients use that same manifest.

Admission amendment, 17:17 UTC, before any successful manifest or utility task: the first resolver attempt timed out on the selected `certifi==2026.10.23` file. Its files, output, source, and protocol are retained in `failed-admission-01`. The second attempt uses an explicit `constraints.txt` for ten transitive distributions and shorter network retry limits. The fourteen root requirements and 28-task utility denominator are unchanged. The manifest must include the constraints hash. This is a changed admission input, not an excluded utility failure.

Each utility task uses a fresh target directory, dependency resolution enabled, `--ignore-installed`, no pip cache, and the local snapshot as its sole index. The install must finish successfully, its report must name only the assigned loopback service, installed files must remain inside the target, and a package-specific function must work in a fresh process. Retain the pip report, output, service requests, elapsed time, and target-version check.

The denominator is 28 tasks. Failed admissions, resolver failures, missing dependencies, incompatible Python versions, and failed function checks are retained and explained. A successful result applies only to these roots, the frozen transitive versions, the host platform, and the Python version used.

## Application-state checks

Before and after repeated reads, the manifest and content hashes must match. HEAD, conditional GET, byte-range GET, missing paths, and storage-statistics paths must have fixed application responses. Responses may depend on the caller's request, but never on another client's request history.

Use a separate copied fixture for validation failures. A changed wheel must fail hash verification. Unknown paths must be rejected locally without contacting an upstream. GET and HEAD are the only supported methods. Logs stay on the trusted side and cannot be fetched through HTTP.

These are functional checks of the implementation, not measurements of covert-channel absence.

## Follow-up experiment candidates

After P1 validation, compare the snapshot against the best mutable configuration on a fresh frozen test battery:

1. A sender's package or missing-path reads followed by the receiver's statistics, headers, body, and latency probes.
2. Concurrent sender downloads while the receiver downloads. Compare shared versus separate serving processes and bounded per-tenant concurrency.
3. Package cold-start, positive-cache, and missing-dependency behavior. A missing dependency fails closed during a run; a trusted admission update changes the next epoch's manifest.

Freeze schedules and decoders before held-out observations. Report achieved transfer for named protocols as lower bounds. Failed searches give sensitivity statements and costs, never a service-wide capacity bound.

## Resources and costs

This preparation uses the Mac, public PyPI metadata and approved wheel downloads, and local loopback services. No model calls, VM launch, Artifactory changes, or contact with another user's service. The only new files are under `evidence/P3-mirror`. Preserve the old evidence. Record local process cleanup and close all serving sockets after utility tests.
