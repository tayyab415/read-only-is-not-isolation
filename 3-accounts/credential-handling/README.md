# Credential handling

## Question
How do credentials get into the clients, and how was the exported evidence checked for leaked secrets?

## Setup
- J: one random password per account (`secrets.token_urlsafe(24)`), written to a private mode-600 file on the host. For each request the controller runs `docker exec -i <client> python3 -c <client code>` and sends `{username, password, marker, method, path}` as JSON on stdin. The client code builds the Basic header, sends one request through the gateway and prints the result. Nothing is written to the client's read-only filesystem. The client code is `CLIENT_CODE` in `../J-identity-origin-binding/scripts/campaign.py`. Stored request records show `<secret-redacted>`.
- D: `provision-sandbox.sh` pipes the credential on stdin into `~/.netrc` on the sandbox's tmpfs (mode 600). It stays out of argv and logs, but the agent inside can read the file. See `../../2-networking/D-isolated-harness/scripts/`.
- P3 Linux: the index adapter holds the backend credential and injects it. Client tasks get no password. Adapter configuration and credentials enter through stdin (`../../1-sandbox-hardening/P3-frozen-admission/P3-linux/PROTOCOL.md`, `adapter.py`).
- BC nodes: the per-episode gateway key is an environment variable in the actor container, not stdin. See `../../2-networking/BC-node-isolation/`.

## Result
- J privacy audit: 2,118 exported files checked, no credential matches, no sensitive response headers (`J-privacy-audit.json`).
- H: 460 files checked against 24 known secret patterns (passwords and their Basic encodings), no matches (`H-redaction-verification.json`, `verify_redaction.py`).
- K: 43 exported files scanned for 40 generated secret values, no matches (`../../1-sandbox-hardening/K-edition-logging/data/redaction.json`).
- These are scoped checks against known values, not scans for every secret format.
- The J identity result in `../J-identity-origin-binding/` shows why a password held inside a sandbox is not an identity.

## Files
`verify_redaction.py` (H's check, reads private credential files that are not included), `H-redaction-verification.json`, `J-privacy-audit.json`.

## How to rerun
`verify_redaction.py` needs the private credential files on the lab host. The stdin pattern can be read in `campaign.py` and tried against any HTTP service.

## Not included
No credential files, password lists or exports of the private state files. Raw logs available on request.

## Edited for release
None.
