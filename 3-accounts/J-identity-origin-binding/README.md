# J identity: copied credentials, with and without origin binding

## Question
With one repository and one Read grant per client, can a client that holds a copy of its peer's password read the peer's repository? And does a gateway that maps each client's network origin to the one account it may present stop that? Paper Section 4.2.

## Setup
The `partitioned` arm of the J campaign: a sender account with Read on its own repository (`...-partitioned-tx`) and a receiver account with Read on its own (`...-partitioned-rx`). No groups, no admin. Grants are in `data/permission-*-partitioned-*.json`. Passwords are random and held in private host files. The test (`scripts/identity.py`, seed 2026110803) runs 16 trials in a shuffled order, 8 from each client. Each trial reads the peer's repository with the peer's copied password, then reads the client's own repository with its own. Arm 1 is account-only. In arm 2 the gateway policy gets `origin_binding`, a map from each client's isolated-network IP to its account (`data/origin-bound-policy.json`), and only the gateway restarts. Artifactory still checks passwords. The copy is supplied by the host on purpose; no credential was stolen. Run 2026-11-08 UTC.

## Result
- Account-only: copied-credential reads returned 200 in 16 of 16 trials.
- Origin-bound: copied-credential reads returned 403 in 16 of 16 trials, rejected by the gateway before forwarding.
- Paired ordinary downloads of the client's own object returned 200 in 16 of 16 trials in both arms, with one identical body hash (`c9018cc1...3449`).
- Wrong-password controls returned 401 in all 4 cases (2 per arm).
- Related J result: eight primes sent with the sender's own credentials against the receiver's repository returned 401, and the receiver made an upstream lookup in all 16 trials (decoder 8/16, always zero).
- Scope: static addresses, unprivileged clients, a trusted host. It does not cover NAT, address reuse, privileged clients or copies made inside one sandbox. Injecting the credential at the gateway, so the sandbox never holds one, is the stronger form and was not part of this test.

## Files
`scripts/identity.py` (the test), `scripts/campaign.py` (account and repository setup, the stdin client), `scripts/gateway.py` and `gateway-unbound.py` (gateway with and without binding). `data/`: `identity-preregister.json`, `identity-trials.jsonl`, `identity-negative-controls.jsonl`, `identity-finished.json`, `origin-bound-policy.json`, `gateway-policy.json`, `initial-state.json`, grant records.

## How to rerun
Needs the lab: the J containers, the Artifactory fixture and its private credential files. `identity.py` refuses to start unless the main run and load follow-up finished (`followup-finished.json`). Run `campaign.py setup`, `campaign.py run` and `followup.py` first (see `../../1-sandbox-hardening/J-control-matrix/README.md`), then `python3 scripts/identity.py`. The identity checks in J's `verify.py` need the response bodies, which are not included.

## Not included
Response bodies for the 68 identity requests (34 per arm) and the request and log-match ledgers (shared J files, see the J folder). Raw logs available on request.

## Edited for release
`scripts/campaign.py`: one docstring said "hub", now "lab host". It is listed in J's `bundle-sha256.json`, so that hash refers to the original.
