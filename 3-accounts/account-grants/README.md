# Account grants

## Question
What can a Read-only account do on this Artifactory, and what does adding Deploy/Cache change? The paper's clients get Read only. These logs show what the extra grant changes.

## Setup
`scripts/art-users.py` creates non-admin users `harness-agent1` to `harness-agent3` with no groups, and one permission target. The legacy REST endpoints for users and permissions are gated to Pro on OSS, so the script uses the web UI's `/ui/users` and `/ui/permissiontargets` endpoints, which work. Random passwords go to a private mode-600 file. The action string for Deploy/Cache is `upload`; unknown strings are dropped without error, so the grant must be read back. `scripts/art-perm-test.sh` and `scripts/art-access-test.sh` run from inside a sandbox. Run 2026-11-07 UTC.

## Result
- Read + Deploy/Cache (`logs/art-perm-agent1-upload.txt`): an uncached fetch through a remote repository returned 200 and the cache folder showed `createdBy: harness-agent1`. A second fetch returned 200 in 4.8 ms. MKCOL of a folder and a nested folder in the cache returned 201. A repository the user had no grant on returned 401. DELETE returned 403.
- Read-only (`logs/art-perm-agent1.txt`): the same uncached fetch returned 403 "not permitted to deploy", and MKCOL returned 403. This run came from a mistaken `deploy` action string that stored only `read`, which makes it a clean Read-only control.
- Sandbox view (`logs/art-access-agent1.txt`): no anonymous access (401); the user lists only its own repository; `/api/security/users` returns 403.
- In J, accounts with Read only got PUT 405 at the gateway and MKCOL 403 (twelve) or 501 (two) directly.
- An early run briefly granted delete and manage on a test repository to check which action names stick. All grants and the throwaway target were then removed.

## Files
`scripts/art-users.py`, `scripts/art-perm-test.sh`, `scripts/art-access-test.sh`, and the three logs above.

## How to rerun
Needs an Artifactory OSS instance, an admin credential file and a sandbox from `../../2-networking/D-isolated-harness/`. Usage is in the header of `art-users.py`.

## Not included
Nothing from the permission work. The rest of the D harness is in the networking section.

## Edited for release
Lab host name and home paths replaced in `scripts/art-access-test.sh` and `logs/art-access-agent1.txt`. No manifest hashes these files.
