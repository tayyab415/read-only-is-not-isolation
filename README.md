# Read-Only Is Not Isolation

Code, frozen protocols and results for the paper *Read-Only Is Not Isolation: Measuring and Closing Shared-State Channels Between Agent Sandboxes* ([paper/submission.pdf](paper/submission.pdf)), written at Apart Research's Swarm Alignment Hackathon, November 6 to 9, 2026.

In the 2026 Hugging Face incident, sandboxed agents used a shared package cache as a message board by creating folders. The obvious fix is to make agent accounts read-only. We rebuilt the setting on our own Artifactory OSS 7.161.26 deployment and tested that fix: can agents that may only read from a shared service still pass information to each other, which settings stop it, what monitors can see, and do language-model agents find and use it?

## Main results

| Finding | Key number | Folder |
| --- | --- | --- |
| Read-only clients still pass bits, through download statistics and through the timing of cached misses. Each single setting leaves one channel open. Blocking the storage API at the gateway plus turning negative caching off closes both, and installs still work. | Single settings: 24/24 or 32/32 bits. Combined: timing 16/32 (chance). 14/14 pinned installs passed. | [1-sandbox-hardening/J-control-matrix](1-sandbox-hardening/J-control-matrix/) |
| Separate repositories need identity bound outside the sandbox. | Copied peer password: 16/16 reads succeed without origin binding, 16/16 rejected with it. | [3-accounts](3-accounts/) |
| Models use the channel when they are told to exchange values. | 110/120 verified exchanges on the open service, 0/120 under the combined control. | [5-agent-swarms/N-hint-ladder](5-agent-swarms/N-hint-ladder/) |
| A generic leakage meter, frozen before a sealed holdout was opened, finds the channels without being told where they are. | Open service 590/1,000 (59.0%, p = 0.0005). Hardened and null arms 49.0% to 51.7%. | [4-leakage-measurements/P1-leakage-meter](4-leakage-measurements/P1-leakage-meter/) |
| Concurrent load still leaks through the hardened designs. | Separate repositories 942/1,000. Private immutable serving 562/1,000. Idle A/A 517/1,000. | [4-leakage-measurements/P2-concurrent-load](4-leakage-measurements/P2-concurrent-load/) |
| Further per-tenant changes also leave the load-timing signal. These were one gateway per sandbox, a 12 ms response floor, and different content for the two tenants' hot objects. | Per-tenant gateways 197/300 and the 12 ms floor 216/300, against an idle control of 146/300. Distinct content 267/300, against idle controls of 163/300 and 141/300. | [P2b-timing-attribution](4-leakage-measurements/P2b-timing-attribution/), [P9-content-hash](4-leakage-measurements/P9-content-hash/) |
| Controls aimed at one piece of state do close it. | A shared 404 cache in NGINX or Squid decoded 29/32 to 32/32. Keying it on the gateway's identity, a private cache or no cache gave 16/32 (chance), and 60/60 ordinary GETs still worked. The host CPU counter was readable at 64/64; a fixed file bound over it gave 32/64. | [P7-http-caches](4-leakage-measurements/P7-http-caches/), [P8-host-telemetry](4-leakage-measurements/P8-host-telemetry/) |
| A frozen, hash-checked package set keeps dependency installs working. | 28/28 installs (macOS), 28/28 in each Linux serving route. | [1-sandbox-hardening/P3-frozen-admission](1-sandbox-hardening/P3-frozen-admission/) |
| On a shared GPU model server, a gateway-assigned cache salt closes the prefix-cache channel, but load still carries a signal. | Prefix cache decoded at 1.000, with salt 0.498. Load decoded at 1.000, about 10 bits/s at 100 ms symbols. | [4-leakage-measurements/S-model-server-gpu-load](4-leakage-measurements/S-model-server-gpu-load/) |
| Server-side monitors see exposure, not intent. | Rule M1 flags 110/110 exchanges and 19 of 60 benign agents. | [6-monitors/O-server-rules](6-monitors/O-server-rules/) |
| Independent copies of a model often share a signalling code without talking. | Full 16-value agreement: 0.49 same model, 0.32 different developers. Opaque file names raise cross-developer agreement to 0.559. | [5-agent-swarms/T1-shared-conventions](5-agent-swarms/T1-shared-conventions/) |
| On a shared model server, agents doing ordinary work felt their peer's load but never treated it as a channel. Agents told to pass a value tried every time and almost always failed, because sender and receiver never agreed on a scheme. | Ordinary work: 0/96 attributions, attempts or transfers. Requests were 1.27x slower while the peer was busy. Told to pass 4 bits: 1/48 recovered, and that one was a guess. One agent-written sender, replayed alone, decodes 15/16 on a shared GPU and 0/8 on separate GPUs. | [5-agent-swarms/BC-agent-discovery](5-agent-swarms/BC-agent-discovery/) |

On both services the pattern is the same. Each control closed the state it was aimed at, and none of the controls we tried brought shared load to chance. [4-leakage-measurements/ISOLATION-RESULTS.json](4-leakage-measurements/ISOLATION-RESULTS.json) records the nine tested properties in machine-readable form.

Every folder README gives the question, setup, result with sample sizes, the files, and how to rerun it.

## Layout

```
1-sandbox-hardening/     client container settings, the control matrix, frozen package admission
2-networking/            per-client networks, the gateway, the upstream fixture, isolation tests
3-accounts/              one account per sandbox, credential delivery, copied-credential tests
4-leakage-measurements/  how much information moves through shared state, layer by layer
5-agent-swarms/          language-model agents working against the shared service
6-monitors/              server-side rules, an exposure budget, a per-call classifier
paper/                   paper source, PDF and figures
explainer/               a short visual walk-through of the first channel and its fixes
```

## The lab

All experiments ran on owned fixtures on AWS: Artifactory OSS 7.161.26 at a pinned digest, a stateless upstream whose missing paths answer 404 after a fixed delay, and a gateway we wrote. Each client is a non-root container with a read-only root filesystem, no Linux capabilities and its own internal network whose only route is the gateway. The gateway forwards only GET and HEAD to the test repositories and logs every request. Credentials reach clients through stdin. No incident payload was replayed and no outside system was a target. Some later studies use different limits; [2-networking](2-networking/) lists them.

## Rerunning

- Analysis and verification scripts run offline wherever their data is included. For example, `1-sandbox-hardening/J-control-matrix/scripts/analyze.py data` reproduces `data/analysis.json` exactly.
- Collection needs the AWS lab (Artifactory and Docker) and, for the agent studies, paid model calls through AWS Bedrock or the Hugging Face router. Each README says which.
- The scripts were written for one `artifactory-lab/evidence/` tree, and folders here were renamed for reading. Some scripts import sibling studies by their original names. [4-leakage-measurements](4-leakage-measurements/) and [5-agent-swarms](5-agent-swarms/) give the commands to rebuild that layout.
- Raw trial files over about 2 MB are left out, including the sealed P1 and P2 holdouts (95 to 121 MB each). Their SHA-256 hashes are in each study's freeze files. Raw logs are available on request.
- A few files were edited for release: home paths, host names and a default admin password. Each README lists its edits. Hash manifests refer to the original files.

## Not included

AI Village data, Swarm Traces rows, third-party papers and fetched web pages, model weights, and any credential. The lab accounts and keys were temporary and only valid inside the lab.

## Sprint work only

All code, data and text here were produced during the hackathon, November 6 to 9, 2026. Nothing was written before it.

## Dual use

This repository contains scripted senders and decoders for covert channels through shared service state: download statistics, cached-miss timing, HTTP cache state, host CPU counters and GPU load. They are written for, and were only run against, the owned fixtures described here. The settings that close or bound each channel are in sections 1 to 4, and paper Appendix A discusses dual use.
