# Version and edition logging comparison

Compare ordinary permissions and observability on OSS 7.133.6 and JCR 7.133.6 with A's verified OSS 7.161.26 findings. This tests configuration behavior, not a vulnerability or historical exploit.

Use a dedicated temporary EC2 instance, no IAM role, localhost service ports, a private local fixture, and fresh database/storage for each image. Pin measured image digests. Leave the lab host and other workstreams untouched. Cap the instance lifetime at one hour with instance-initiated shutdown configured to terminate, then verify termination and delete its dedicated security group.

For each healthy image, record edition/version, effective grants, read-only and Read + Deploy/Cache `MKCOL` outcomes, listing membership, statistics visibility, request/router/audit logs, and database folder attribution. A failed startup or unsupported repository type is retained as such. It is not evidence that a feature is impossible.

Reserve $1 for the short instance/storage lifetime. Research delegates and VM charges remain separately identified.
