# Client container hardening

## Question
What can a client container do, and what can it reach? This is the sandbox description in paper Section 3.

## Setup
`compose.yaml` is the J client definition. Each client runs `python:3.12-slim` as UID 20001 with a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, 128 MiB memory with no extra swap, 0.5 CPU, 48 processes and a 16 MiB `/tmp`. Each client has its own `internal: true` network with `gateway_mode_ipv4: isolated`, so the host bridge has no address on it. The gateway is the only other member of that network.

## Result
- Process status inside both clients: `Uid 20001`, `CapEff` and `CapBnd` all zero, `NoNewPrivs 1` (`probes/network-ip-probes.json`).
- Connectivity: the sender and receiver reached the gateway on port 8000. The Artifactory, upstream and peer names did not resolve. `169.254.169.254:80` and `1.1.1.1:443` failed (`probes/connectivity.json`).
- All 8 probes by raw IP (Artifactory backend, upstream, peer gateway address, metadata address; 4 per client) returned errno 101, network unreachable.
- Explicit writes failed. PUT returned 405 at the gateway. MKCOL with Read accounts returned twelve 403s and two 501s (J report).
- A plain `--internal` Docker network is not enough. In the D harness a probe on one reached the host's sshd through the bridge gateway address. The isolated gateway mode removed it. See `../../2-networking/D-isolated-harness/`.
- These are measured IPv4 checks, not an escape assessment. IPv6, kernel escapes and host-resource contention were not tested.

## Files
- `compose.yaml`: client, gateway and upstream definitions, identical to `../J-control-matrix/compose.yaml`.
- `probes/containers.json`: `docker inspect` settings for each container (user, read-only root, capabilities, memory, pids, networks).
- `probes/connectivity.json`, `probes/network-ip-probes.json`: reachability checks from each client.
- `probes/networks.json`, `probes/image-identities.json`: network definitions and image IDs.

## How to rerun
The probes ran inside the containers during the J campaign, so they need the lab host. `../J-control-matrix/scripts/campaign.py` runs the connectivity check as part of `setup` and `run`. The broader 72-test script is `../../2-networking/D-isolated-harness/scripts/isolation-test.sh`.

## Not included
Nothing from this set. The remaining J files are listed in `../J-control-matrix/README.md`.

## Edited for release
`probes/containers.json`: home-directory paths in bind mounts changed from the lab user's name to `/home/<user>`. The original is listed in J's `SHA256SUMS.json`, so that hash refers to the unedited file.
