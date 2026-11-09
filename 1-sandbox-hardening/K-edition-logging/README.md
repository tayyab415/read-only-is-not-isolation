# K: edition and logging comparison

## Question
Do Artifactory OSS 7.133.6 and JCR 7.133.6 let a Read + Deploy/Cache account create folders with the same attribution gap seen on OSS 7.161.26, where the request log shows the MKCOL but there is no deploy event and no creator?

## Setup
Two fresh stacks (Artifactory plus PostgreSQL) on a temporary EC2 instance, one per edition, pinned by image digest: OSS `sha256:e2f7cab91bade50fae3175e8c1ad0b8a48c6b585bbb16b06b8f1d187123fd404`, JCR `sha256:55edcdeb55f92aba5ff223c526056db772e7d946669e74ceb27049750fc2c298`. A harmless local upstream. The controller sent authenticated requests from inside the server container with credentials on stdin. This compares permissions and logs only. Network isolation comes from D, H and J. Run on 2026-11-08 UTC.

## Result
| Check | OSS | JCR |
| --- | --- | --- |
| Generic local and remote repository creation | accepted | accepted |
| Read-only MKCOL, local and remote-cache paths | 403 | 403 |
| Read + Deploy/Cache MKCOL, both locations | 201 | 201 |
| Folder metadata and database node afterwards | present | present |
| Database created_by / modified_by on the folder | NULL / NULL | NULL / NULL |
| Accepted-deploy event for the folder | absent | absent |
| Positive deploy controls (seeded files) | 2 | 2 |
| Read-only file download statistics | 200 | 200 |
| Deep list `?list&deep=1` | 400 | 400 |

- 49 requests in all, 24 on OSS and 25 on JCR, each joined to the Artifactory request log, the router log and a trace ID (`verification.json`).
- Folder creation is confirmed by the later metadata response and the database row, not inferred from the 201.
- Deep-list returned 400, so folder enumeration was not tested. Properties, anonymous access, other versions and commercial editions were not tested.
- 43 exported files scanned for 40 generated secret values, no match (`data/redaction.json`).
- Cost: about 26 minutes of an m6a.large at $0.05555/h, roughly $0.025, a rate estimate.

## Files
`PROTOCOL.md`, `launch.py` and `cleanup.py` (create and remove the instance and its security group), `run_versions.py` (stack setup and requests), `verify.py`, `redact_remote.py`, `verification.json`. `data/oss-7.133.6-routed/` and `data/jcr-7.133.6-routed/`: `requests.jsonl`, request, router and access logs, database nodes, image metadata, pull output. `data/summary.json`. `recheck/K.out` is the output of an earlier rerun of `verify.py`.

## How to rerun
`launch.py`, `run_versions.py` and `cleanup.py` need AWS credentials, boto3 and an SSH key, and they create a billable instance. `verify.py` first hash-checks every file listed in `data/redaction.json`, including files not in this copy and two `image.json` files edited for release, so it stops at that step here. The join and attribution checks after it read only the included logs.

## Not included
19 files, 10.0 MB: `data/readiness-control.json` (9.3 MB readiness probe dump), the two initial-attempt directories (15 files, stack readiness failed through the host loopback path), `instance.json` and `cleanup.json` (AWS ids and a public IP), `ec2-pricing.json`. Raw logs available on request.

## Edited for release
`launch.py` and `cleanup.py`: lab host instance id replaced by `<LAB_HOST_INSTANCE_ID>`, variable `hub` renamed. `run_versions.py`: the vendor default admin password literal replaced by `<DEFAULT_ADMIN_PASSWORD>`; set it to the JFrog default before running. `PROTOCOL.md`: two budget phrases reworded. `data/*-routed/image.json` (listed in `redaction.json`): a vendor maintainer email removed.
