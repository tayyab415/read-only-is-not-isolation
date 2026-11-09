# 1. Sandbox hardening

This section holds the container settings for the clients, the control matrix that tests each single setting against the two read-only channels, and the frozen-admission design that serves a fixed package set with no shared mutable state. It also holds a logging comparison on two older Artifactory editions and a read-back of how Artifactory stores identical bytes under separate repositories. Paper Sections 3, 4.1 and 4.7 draw on it.

| Folder | Question | Result |
| --- | --- | --- |
| [client-container-hardening](client-container-hardening/) | What can a client container do, and what can it reach? | UID 20001, capabilities all zero, NoNewPrivs 1. All 8 probes by raw IP (backend, upstream, peer, metadata address) failed with errno 101. |
| [J-control-matrix](J-control-matrix/) | Which single setting closes the statistics channel and the negative-cache channel? | Each single control leaves one channel open (24/24 or 32/32). Storage API blocked plus negative caching off: statistics unavailable, timing 16/32, which is chance. 14/14 pinned installs passed. |
| [P3-frozen-admission](P3-frozen-admission/) | Can a frozen, hash-checked package set serve dependency-resolving installs? | 24 wheels, 5,013,675 bytes. 28/28 installs on macOS. Linux, two serving routes: 28/28 in each. |
| [K-edition-logging](K-edition-logging/) | Do OSS and JCR 7.133.6 show the same folder-creation attribution gap? | Yes. Read-only MKCOL 403, Read + Deploy/Cache MKCOL 201, creator NULL, no accepted-deploy event. 49 requests joined. |
| [P2-storage-native-check](P2-storage-native-check/) | Do separate repositories still share stored bytes? | 32 cached paths in two repositories map to one 31-byte file in the filestore. |

All experiments ran on owned fixtures between Nov 6 and Nov 9, 2026. Where a folder lists numbers, they come from the paper or from the retained analysis files named in that folder.
