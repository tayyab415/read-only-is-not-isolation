# Gateway

## Question
What does the gateway between each sandbox and Artifactory enforce, and what does it log?

## Setup
A small Python HTTP proxy in four editions, all with the same shape. Clients can reach only this process. It listens on port 8000 and forwards to Artifactory's router port 8082.
- `J-controls/gateway-unbound.py`: the first edition, no identity binding.
- `J-controls/gateway.py`: adds `origin_binding`, a map from client IP to the one account name that may be presented. Inactive unless the policy file has it.
- `P1-meter/gateway.py`: binding always on, a `principal` field in the log, storage metadata blocked per repository.
- `P2-residual/gateway.py`: edition 2. Adds the incoming conditional and Range headers and a body SHA-256 to each record, a configurable backend and a listen queue of 5 or 128.

## Result
- Checks in order: origin identity (403), method other than GET and HEAD (405), repository scope (403), path traversal or backslash (403), storage API where blocked (403), any other `api/` route (403). Forwarded headers are `Authorization`, `User-Agent`, `If-None-Match` and `If-Modified-Since`, plus `Range` in the P1 and P2 editions. Redirects are not followed.
- Each request writes one JSON line: start and end time in ns, method, full path with query, status, policy reason, body bytes, client IP and the `User-Agent` marker. P1 and P2 add `principal`. The J edition does not log a principal, so J joins Artifactory's request log for the authenticated name.
- J: 1,946 of 1,946 client requests matched exactly one gateway entry, and none of the 126 gateway-blocked requests reached Artifactory. An ordinary info request and a `?stats` request for the same object appear in Artifactory's log with the same query-free path, while the gateway keeps the full target (`J-query-audit.json`).
- `python3 -m unittest test_gateway` in `P2-residual` runs 5 tests (query and wire headers preserved, storage block has no backend action, borrowed identity rejected before the backend, writes and encoded traversal fail, HEAD delivers no body and a matching hash). They pass on this copy.

## Files
`J-controls/`, `P1-meter/`, `P2-residual/` as above. `J-query-audit.json` is the field comparison for the info and stats responses.

## How to rerun
The gateway needs a policy file at `/gateway.json` (repository names, `block_metadata` or `block_storage`, optional `origin_binding`) and an Artifactory host named `art-a`. The J campaign scripts in `../../1-sandbox-hardening/J-control-matrix/` write that file and start it. The unit tests need nothing else.

## Not included
Nothing excluded. The N-study gateway edition is not copied here.

## Edited for release
None.
