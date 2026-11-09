# D: isolated harness

## Question
Can three sandboxes be cut off from the internet, the cloud metadata service, the host, the Docker socket and each other, while each still reaches Artifactory? And does a tool-call log see everything a sandbox sends to the server?

## Setup
`compose.yaml` defines three sandboxes, each alone on its own internal network (`172.30.11.0/24`, `172.30.12.0/24`, `172.30.13.0/24`) with `gateway_mode_ipv4: isolated`. The Artifactory container is attached to all three, so it is the only shared peer. Each sandbox runs as UID 20001 with a read-only root, all capabilities dropped, `no-new-privileges`, 2 GiB memory with no swap, 1 CPU, 256 processes, and tmpfs `/home/agent` (1 GiB) and `/tmp` (256 MiB). These limits are larger than the 128 MiB J clients. `scripts/controller.py` runs scripted commands by `docker exec` and logs microsecond timestamps taken inside the sandbox. Run on 2026-11-07 and 08 UTC.

## Result
- `scripts/isolation-test.sh` passes 72 of 72 tests in each of the three sandboxes (7 logs: before attaching Artifactory, after, and on the final image). Areas: internet, DNS, metadata service, host services, Docker, other sandboxes, host files, privileges, resource limits.
- Positive controls show the probes can fail: a default-bridge container got HTTP 200 from the internet, answered external DNS and obtained a metadata token (200).
- A leak found on the way: on a plain `--internal` network a container reached the host's sshd through the bridge gateway address. Setting `gateway_mode_ipv4=isolated` removed it.
- A sandbox reaches Artifactory's ports 8081 and 8082 on its own network and nothing else.
- Tool-call log gap: 13 Artifactory requests came from sandbox addresses. 6 were issued by tool calls and 7 by background loops with no tool call. All 7 fall inside some other call's time window, so matching on time alone mislabels them (`logs/gap-join.txt`).
- npm and pip installs of GitHub source archives through a remote repository worked from a sandbox with no internet (`logs/install-via-github-remote.txt`). PyPI and npm-registry installs through Artifactory were not tested.
- Open risks noted in the run: the Artifactory container has `ip_forward=1` and sits on all three networks, so a sandbox with root or network capabilities could cross; and the sandbox's `.netrc` credential is readable by the agent.

## Files
`compose.yaml`, `image/Dockerfile` (sandbox image), `scripts/` (`attach-art.sh`, `provision-sandbox.sh`, `isolation-test.sh`, `controller.py`, `gap-join.py`, `install-via-github-remote.sh`), `plans/` (smoke and gap-demo command lists), `logs/` (isolation output per sandbox, gap demo and join, smoke run, install run). Account setup and the permission test are in `../../3-accounts/account-grants/`.

## How to rerun
Needs a Docker host with the sandbox image built, an Artifactory 7.161.26 container named `art-a`, and the harness user credentials. `docker compose up -d`, `bash scripts/attach-art.sh`, `bash scripts/provision-sandbox.sh`, then `bash scripts/isolation-test.sh N` for sandbox N. These runs need the lab host.

## Not included
8 files, 0.02 MB: scripts and logs from a failed attempt to start a second Artifactory (out of memory), a repository-type probe that did not discriminate, and a test plan that was written but never run. Raw logs available on request.

## Edited for release
Lab host name, home paths and two public example.com addresses replaced in `logs/`, `scripts/` and `compose.yaml` (`lab host`, `/home/<user>`, `<public-ip>`). Test output is otherwise unchanged. Nothing here is hashed by a manifest.
