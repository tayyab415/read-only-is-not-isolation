# 2. Networking

Each client sits alone on an internal Docker network whose only route is a gateway we wrote. The gateway forwards GET and HEAD to the test repositories, blocks everything else, and logs each request. This section has the gateway code in four editions, the delayed-miss upstream fixture, the three-sandbox harness, the first statistics measurement, and the per-node isolation used for the later model-server runs. Paper Section 3 describes the lab.

| Folder | Question | Result |
| --- | --- | --- |
| [gateway](gateway/) | What does the gateway enforce and log? | Checks run in order: origin identity, method, repository scope, path, then storage and other API routes. 126 of 126 gateway-blocked requests in J never reached Artifactory. |
| [upstream-fixture](upstream-fixture/) | What does the upstream do on a missing path? | Answers 404 after a fixed delay (150 ms or 20 ms in J), keeps no state. |
| [D-isolated-harness](D-isolated-harness/) | Can sandboxes reach Artifactory and nothing else? | 72/72 isolation tests passed in each of 3 sandboxes. Plain `--internal` networks left the host's sshd reachable until the isolated gateway mode was set. |
| [H-infra-first-channel](H-infra-first-channel/) | Does a read-only user pass bits through download statistics? | Shared path 32/32. No-sender controls 0 of 16 positive. Separate paths 0 of 32 positive. 433 requests joined to server logs. |
| [BC-node-isolation](BC-node-isolation/) | How is a node's tenant slot isolated from the host and the other slot? | Canary probes held in all 4 validation arms; no foreign tenant seen in any gateway log. |

Container limits differ by experiment. The J and H clients use 128 MiB, as in the paper. The D sandboxes use 2 GiB, and the BC actors use 1 GiB with a writable root.
