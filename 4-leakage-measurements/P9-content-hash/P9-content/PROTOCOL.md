# P9: checksum sharing between the two hot objects

Selected 2026-11-09 after complete P2b analysis, independent verification,
idle-control review and a scoped native storage readback. This is a fresh
conditional follow-up. P2 and P2b data informed its design; no P9 data exist.
The scientific source freeze and checks must precede its first native smoke.

## Question and intervention

Does checksum sharing between the sender's hot `pkg-00.bin` and receiver's
hot `pkg-15.bin` contribute to their concurrent HTTP timing association?

All configurations use separate repositories and one P2 gateway per tenant,
with the same aggregate gateway quotas. Artifactory and the host remain
shared. Actor limits, network controls, request grammar and schedules
derive from completed P2b edition 4. No global Artifactory setting, shared
service, original frozen source or existing fixture is modified.

Four fresh configurations:

| Cell | Sender hot object 0 | Receiver hot object 15 | Payload execution |
| --- | --- | --- | --- |
| `equal` | A | A | active for label one |
| `distinct` | B | A | active for label one |
| `aa-equal` | A | A | idle under both labels |
| `aa-distinct` | B | A | idle under both labels |

A is the original harmless 31-byte fixture
`p1-meter fixture package bytes\n`. B changes only its first byte to `q`,
also 31 bytes. Both actors' object 0 uses the cell's specified payload,
so changing sender direction requires no actor-specific serving behavior.
Objects 1 through 15 remain A in every cell. Receiver object 15 therefore
keeps its bytes and ETag across cells and labels. Setup explicitly reads
that object's actual ETag; it must not use object 0's distinct ETag.

This changes sharing between the two measured hot objects. The rest of the
catalog still shares A. It is not a comparison of fully actor-distinct
catalogs, caches or physical disks. Sender response metadata may change
with its checksum. A result concerns the complete object-content intervention,
not a uniquely identified internal cache or page-cache operation.

## Native mapping and boundaries

Before and after every cell, query exactly its two owned cache repositories
inside a read-only database transaction. Require all thirty-two expected
nodes, object lengths and per-object SHA-1/SHA-256 values. Verify the
matching canonical filestore objects by native SHA-1/SHA-256 and stat.
Equal cells must have one expected checksum; distinct cells must have two,
with distinct canonical file identities. These are scoped canonical-file
checks, not a global no-duplicate-copy or page-cache-residency claim.

Retain the exact rendered upstream source and its hash, mounted source
readback, actual container/image identities, runtime boundaries and all
native before/after mapping evidence. Rendering changes one fixed cell
constant, never a trial label. If the source, mapping or expected response
bytes fail, preserve the failure and stop. Do not infer successful storage
separation from client headers alone.

All client requests must join exactly one assigned gateway row and,
when forwarded, one backend and router record with matching trace,
principal, full target, status, body and interval. Preserve the original
2 ms tolerance. Live route controls include own gateway/backend reachability,
peer gateway denial, direct backend denial, peer-listener positive/negative
controls and proven listener stop. Both gateways have separate client
networks plus the service network. No client reaches the other gateway.

Verify every sender GET returns its specified object 0 bytes and every
successful receiver object 15 GET returns A. Receiver HEAD has the existing
custom gateway's zero-length serving defect, already reported in P5b.
It remains identical across cells; this experiment does not repair it or
claim general serving correctness. Post-window folder/statistics/missing
probes retain their original grammar and status behavior.

## Fixed collection

Use quiet owned lab2 only. The labhost may run P8 because it is a different
reserved host. One workload per host under its own common P2 collection
lock. No new VM, provider call, model, incident reproduction or external
experiment target.

The sender has 128 own object 0 GETs, concurrency eight, scheduled over
two seconds. A private execution flag executes them only for active
labels in active cells. Idle controls receive the same command list but
execute no sender HTTP under either randomized label. The receiver has
the same fifteen timed GET/HEAD probes and six post-window probes as P2b.
Both workers start independently against one future epoch; receiver
dispatch never waits for sender completion.

Use eight smoke, one hundred calibration and three hundred held trials
per cell, balanced within the two sender directions and independently
shuffled per split/cell with fixed seed 2026110909. Every split uses fresh
owned fixture IDs. Smoke never enters fitting or scientific results.
Late dispatches, sender overruns, failed requests and incomplete work stay.
No adaptive retries, cell replacement or exclusion is permitted.

Each complete split must pass its native audit and cleanup before the next
stage. All four complete calibration cells must be locally transported and
audited before fitting. Freeze every original input, source and all four
models together before any fresh held collection or access. Never fit
after a held file exists.

No held collection starts after 07:45 UTC. If readiness cannot meet that
window, report the study as unrun or incomplete. Stop new owned work by
09:30 UTC, allowing public retrieval and the fixed 10:30 UTC lab2 teardown.
Do not kill a healthy existing controller to satisfy another study.

## Statistic, tests and predictions

S is the median elapsed time of the fifteen fixed timed receiver probes.
For each cell, calibration sets its threshold to the midpoint of active-
label and idle-label medians, with the direction given by their order.
Strict threshold ties predict zero, preserving the P2b rule.

Primary tests use the exact conditional assignment distribution with
balanced labels within each sender direction and fixed held predictions.
Use the upper tail of correct predictions and Holm correction across
all four cells at 0.05. Both physically idle cells must have raw and
corrected p-values at least 0.05 before active-arm interpretation.
This is P9's prospective primary family; P2b's binomial primary family
is unchanged. Separate arithmetic verification uses integer combinations
rather than the primary hypergeometric/convolution implementation.

The single secondary comparison is equal versus distinct active-cell
accuracy by two-sided Fisher exact test. Retain both accuracy difference
and p-value. No accuracy reduction is claimed from nonsignificance or a
near-chance distinct score alone. Sequential cells and one shared host
limit attribution; no new uncorrected latency-discovery tests are added.

The directional hypothesis is lower activity discrimination in the distinct
cell if sharing the two hot objects' content contributes to the observed
effect. Surviving discrimination in both cells would show that this
intervention is insufficient. A failed equal-cell baseline or idle control
prevents the intended positive attribution. A failure is reported, never
tuned away. Native mapping remains a layout result even if timing is
inconclusive.

Report all assigned denominators, fixed-decoder confusion counts, true/
false-positive rates, nominal Wilson 95% intervals under the binomial
assumption, primary corrected results, the secondary comparison, actual
trial periods, complete scheduling diagnostics and native storage/request
provenance. No capacity, message rate, autonomous communication, intent,
whole-host isolation or unique internal causal mechanism is claimed.

## Gates, costs and ownership

Publication requires the actual complete verified P2 and P2b evidence,
passing primary idle controls, full native audits, unchanged source/model
hashes, public cleanup proof, quiet lab2 and available memory. Decisions
must hash real copied evidence; planned booleans are insufficient.

Before native smoke, preserve the existing cleanup/inventory controllers
and prospectively expand their exact public-evidence contract for this
owned study. It must also support retrieval of any stopped cell's retained
public diagnostics. Private runtime configs, credentials and T3 histories
remain local/private and are excluded from transport.

P9 consumes already reserved lab2 time, makes zero provider calls and does
not increase the $38.1959800672 all-in reservation. Full lab2 allowance
remains $3.20 and the total user ceiling remains $50. Reservations are not
invoices. Retain every failure, source edition and cost/cleanup readback.
