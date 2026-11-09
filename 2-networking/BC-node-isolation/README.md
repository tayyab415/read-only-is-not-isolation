# BC node isolation

## Question
Each GPU node runs two tenant slots in front of one model server. Can a slot reach only its own gateway, and does the gateway set the tenant's identity itself? This is the same pattern as the Artifactory lab (one network per tenant, a gateway, identity set outside the sandbox), applied to a vLLM server.

## Setup
`node/provision.sh` builds two internal Docker bridges per node, `bcnet1` and `bcnet2` (`10.201.1.0/24`, `10.201.2.0/24`), with the host side of each bridge as that slot's gateway address. `node/firewall.sh` accepts only TCP 9000 from a slot's subnet to its own gateway and drops the rest of `10.201.0.0/16` in `INPUT` and `DOCKER-USER`. `node/gateway_bc.py` runs once per slot, bound to the slot's bridge address. It reads the slot's tenant from a key file, strips any client `cache_salt`, injects `HMAC-SHA256(secret, tenant)`, forwards only POST `/v1/chat/completions` and `/v1/completions` (plus GET `/v1/models` and `/health`), removes `prompt_tokens_details` from replies and writes one JSON log line per request. `node/node_ctl.py` starts the actor container (UID 1500, all capabilities dropped, `no-new-privileges`, 1 CPU, 1 GiB, 256 processes; the root is not read-only because `/work` is writable). The per-episode gateway key reaches the actor as an environment variable, not stdin. The `canary` command runs 9 probes as root inside the container. Run 2026-11-09.

## Result
- Canary: the own gateway was reachable, and the model server's port on the bridge address, ssh on the bridge address, the other slot's gateway, `docker0` ssh, the metadata address, an internet IP, external DNS and the other slot's container were all blocked. This held in all 8 canary runs (sender and receiver in each of 4 validation arms), and no gateway log showed a foreign tenant (`validation/canary-summary.json`).
- Scripted validation through the node stack, 16 trials per arm, chance 1/16: shared node 16/16 recovered, no sender 0/16, sender and receiver on different nodes 1/16. A first shared attempt scored 10/16. Its sender used 4 threads, `max_tokens` 64 and a 0.05 s margin and spilled into the next slot; the sender was changed and the first run is kept as `try1-spillover` in the summary.
- These controls test a load channel on a shared GPU, not the Artifactory channels, and are not in the paper's Artifactory sections.

## Files
`node/`: `actor.Dockerfile`, `provision.sh`, `fleet_init.sh`, `firewall.sh`, `gateway_bc.py`, `node_ctl.py`, `node_ctl.sh`. `validation/`: `run_controls.py`, `sandbox/sender.py`, `sandbox/receiver.py`, `fleet_readback.py` (checks every node's code hashes, services and firewall rules), `canary-summary.json`.

## How to rerun
Needs GPU nodes built from the node image over ssh, which belongs to the AWS lab. `run_controls.py` and `fleet_readback.py` read an ssh config that is not included.

## Not included
Node inventory with addresses, ssh config and known hosts, the fleet launch and teardown scripts and records, the image record, the pip constraint file, the pre-collection readbacks and logs, the B and C task data and episode harness (see their own sections), and the four full `controls-*.json` files (1.4 to 2.4 MB each, 7.5 MB in all). `canary-summary.json` keeps only the `summary` object of each. Raw logs available on request.

## Edited for release
`validation/canary-summary.json` is a new extract, not a copy. No other file was changed.
