# P2: native storage check

## Question
Separate repositories give each tenant its own namespace. Do two such repositories still point at the same stored bytes? JFrog documents checksum-based storage, so one binary at several paths is stored once. This checks that on the lab's Artifactory 7.161.26 (image digest `sha256:d103c046...eac1d1`).

## Setup
Read-only readback on 2026-11-09 05:14 UTC against a completed fixture with two per-tenant cache repositories, each holding `pkg-00.bin` to `pkg-15.bin` (32 paths). `inspect_owned.py` runs one SQL query in a `BEGIN READ ONLY` transaction on the Artifactory database container, restricted to those two repositories and those 32 names. It then reads the derived filestore path. No request was sent to Artifactory, no fixture was created and no model was called (`new_fixture_created`, `model_calls` in `RESULT.json`).

## Result
- All 32 node records have length 31, SHA-1 `e8627e1a8b3b777cb5e58066954340fdbbc31e2b` and SHA-256 `85c7795747cf1f3ac1e7d7f53d3faa6b9d9f3dc961aa9b692aaaddb943ace870`.
- The filestore holds one matching regular file at `filestore/e8/e8627e1a...`: 31 bytes, hardlink count 1. Native `sha1sum`, `sha256sum` and `stat` agree with the database rows.
- So the two repositories keep separate database rows over one stored file.
- Limits: this does not inventory the filestore, show there are no other copies, measure page-cache state, or show that shared content causes any timing effect. This result is not reported in the paper.

## Files
- `RESULT.json`: every node row, the exact SQL, candidate paths, hashes, stat fields and source hashes.
- `inspect_owned.py`: the readback script.

## How to rerun
Needs the lab host that held the fixture, with the Artifactory and database containers running and the same repository names. The script asserts those names and the provisioning receipt, so it will not run elsewhere.

## Not included
Nothing excluded.

## Edited for release
`inspect_owned.py`: one message string said "hub", now "lab host". `RESULT.json` records the original script's hash (`source_sha256`), so it refers to the unedited file. `RESULT.json` has the same wording change.
