# Upstream fixture

## Question
What does the upstream need to do for the negative-cache channel to be measurable without outside traffic?

## Setup
Small stateless HTTP servers that make no outbound requests. A missing path answers 404 after a fixed delay, so a receiver that hits Artifactory's remembered miss is faster than one that waits for the upstream. Present paths return fixed bytes.
- `J-controls/upstream.py`: `/missing/<delay>/...` sleeps 20 or 150 ms, then returns 404. Other paths return a fixed 40-byte payload with fixed `Last-Modified` and an ETag. One path serves the pinned `six` wheel from `/tmp/six.whl`. One JSON log line per request.
- `J-controls/upstream-matrix.py`: the version used for the main matrix, before the wheel route was added.
- `P1-meter/upstream.py`: 16 present paths `/pkg-00.bin` to `/pkg-15.bin` (31-byte payload), every other path 404 after 150 ms.
- `H-infra/fake_upstream.py`: the first fixture. Role `upstream` or `target` by environment variable. 404 after 150 ms on `/missing/`, a 302 on `/redirect/`, a fixed body elsewhere.

## Result
In J the baseline receiver's median was 164.32 ms when the sender had stayed quiet and 11.76 ms after the sender requested the path, at the 150 ms setting. Upstream logs confirm the cause: unprimed receivers caused one upstream lookup, primed receivers none (32 trials). With negative caching disabled each trial caused a lookup (48 in total).

## Files
The four scripts above. They hold no credentials and read nothing but their request.

## How to rerun
`python3 upstream.py` listens on port 8000. The J compose file mounts it into a client-grade container on the services network (`../../1-sandbox-hardening/J-control-matrix/compose.yaml`).

## Not included
Nothing excluded.

## Edited for release
None.
