# P1: leakage meter

Draft, 2026-11-08. The design is fixed here. The builder may change implementation details, but not the design. Any change goes in `CHANGES.md` before the data it affects. The protocol is frozen (SHA-256 recorded in `FROZEN.json`) after the smoke run and an independent review, and before any calibration data.

## Question

Can a generic differential test find read-state channels between two sandboxes without being told which mechanism to look for?

## Fixture

Reuse the N harness pattern (`../N-ladder/service.py`, `gateway.py`, `upstream.py`) on `art-a` (Artifactory OSS 7.161.26):

- **Per-configuration fixtures:** fresh `p1-*` repositories and users.
- **Gateway:**
  - allows GET and HEAD only;
  - confines paths;
  - binds each client's network address to its account (origin binding);
  - logs the authenticated principal and the full target.
- **Upstream:** a stateless local server. `pkg-00.bin` to `pkg-15.bin` return identical bytes with no delay. Every other path returns 404 after 150 ms.
- **Prefetch:** a separate prefetch principal prewarms all 16 artifacts.
- **Clients:** each is non-root, with a read-only root filesystem, no capabilities, and its own isolated internal network.

Trial loops must run on the labhost, inside or beside the sandboxes, not as one SSH call per request from the Mac. Each client measures latency inside its own sandbox with a monotonic clock. All records carry a trial ID and a request marker, so client, gateway and Artifactory records can be joined.

| Configuration | Repositories | Gateway storage API | Negative-cache period |
| --- | --- | --- | --- |
| `open` | one shared | allowed | 1800 s |
| `combined` | one shared | blocked, 403 | 0 s |
| `separate` | one per client, same upstream; each client has Read on its own repository only | blocked, 403 | 1800 s |

The configurations run one after another, never at the same time, because they share `art-a`.

## What the meter knows

The meter knows two things:

- the action grammar;
- the names both clients can address:
  - the 16 prewarmed artifacts;
  - each trial's rendezvous paths, derived from the trial ID;
  - the repository directory.

It is not told which action, response field or timing carries information.

## Trial

Each trial carries one bit. Labels are balanced and shuffled. Label 1 means the sender is active; label 0 means the sender stays idle for the same window.

1. **Receiver pre-snapshot.** Metadata-only probes of shared state. These must not create state that the post-battery measures. In particular, no first touch of the trial's rendezvous paths.
2. **Sender window (W = 1.0 s).**
   - If active, the sender draws one recipe from the grammar with fixed probabilities, logs it, and executes it.
   - If idle, it sleeps for W.
3. **Settle gap:** 0.3 s.
4. **Receiver post-battery.** A fixed, ordered list of probes:
   - GET, HEAD, storage `?stats`, `?properties` and listing on a fixed subset of shared artifacts;
   - first-touch GET and HEAD of the trial's rendezvous missing paths;
   - a directory listing;
   - five timed GETs of one cached artifact;
   - any other endpoint the gateway lets through.

   Each probe records status, every response header, body SHA-256, length and latency.
5. **Inter-trial gap:** at least 0.5 s, longer after a load recipe.

**Features:** every post-battery value, plus the post-minus-pre delta for every field that also appears in the pre-snapshot. Exclude only fields that are pure clocks (`Date`) or per-request IDs. List the exclusions in `CHANGES.md` before calibration.

## Sender grammar

A recipe is an action applied to a target.

- **Actions:** GET, HEAD, conditional GET (using the cached ETag), 1-byte range GET, and `?stats`, `?properties` and `?list` queries where the gateway allows them.
- **Targets:**
  - one of the 16 artifacts, chosen at random;
  - the trial's rendezvous missing path;
  - the repository directory;
  - a random fresh path.
- **Load recipe:** a burst of 8 concurrent GETs of a cached artifact, repeated through the window.

Fix the recipe probabilities in `recipes.json` before the smoke run. In `separate`, the sender's targets live in its own repository.

## Analysis

The analysis is frozen in `analysis.py` before any holdout data exists.

**Calibration (300 trials per configuration):**
- Run per-feature two-sample tests: Fisher's exact test for categorical features, Mann-Whitney for numeric ones. Apply Holm correction across all features.
- Fit an L2 logistic regression and a gradient-boosted classifier to predict the label. Choose one by cross-validation within calibration. Freeze it and its threshold.

**Holdout (1,000 trials per configuration):**
- Report accuracy with an exact binomial test against 0.5, plus a permutation p-value from 10,000 label shuffles.
- Run a per-recipe breakdown: accuracy on trials with active recipe *r* against all idle trials. Recipe labels are joined only after scoring.

**Reporting:**
- Positive results give the decoder and recipe, the accuracy, and bits per trial (1 − H₂(error)), stated as a lower bound for that protocol.
- Negative results are sensitivity statements:
  - overall, 80% power at about 56.1% accuracy (3 SD, n = 1,000);
  - per recipe, the power implied by its active-trial count.
- Never write "no channel".

## Nulls

These run before the configuration results are interpreted. If a null fails, fix the pipeline first, and record the fix in `CHANGES.md`.

- **A/A:** labels are assigned, but the sender never acts. Run under `open`: 300 calibration and 1,000 holdout trials.
- **Sham:** on label 1, the sender runs its recipe against a dummy HTTP server on its own isolated network, with no route to `art-a`. Run under `open` at the same sizes.
- **Label permutation:** on every run.

A null passes when holdout accuracy is within the permutation null at α = 0.05 and no feature is Holm-significant.

## Meter validation

The meter is validated when all three hold:

1. Under `open`, it detects both known channels: a download recipe on an artifact shows up in the statistics features, and a missing-path recipe on the rendezvous path shows up in rendezvous latency.
2. Under `combined` and `separate`, those two mechanisms are absent: statistics return 403, and rendezvous latency shows no label effect.
3. Both nulls pass.

Any other reproducible signal under `combined` or `separate` is a finding to investigate, not a meter failure.

## Order

1. Smoke: 20 trials per configuration and per null.
2. Independent review of the harness and the smoke run.
3. Freeze the protocol (hash it).
4. Calibration runs.
5. Freeze `analysis.py` and the models.
6. Holdout runs.
7. Analysis.
8. Report: `../../reports/P1-meter.md`.

## Hub rules

- Use `art-a` and owned `p1-*` fixtures only. Do not change existing repositories, global settings, other workstreams' containers, firewall or security group rules, or autostop.
- Publish ports on 127.0.0.1 only. Never use AWS credentials on the labhost.
- Run long jobs in named tmux sessions with logs in `~/logs/`. Check memory with `free -h` before starting.
- Credentials stay in mode-600 files on the labhost and enter clients through stdin only. Never put them in evidence or output.
- When done, remove owned containers and networks, detach `art-a` from P1 networks, keep fixtures and evidence, and save a readback that proves the cleanup.

No model calls. The only cost is labhost compute.
