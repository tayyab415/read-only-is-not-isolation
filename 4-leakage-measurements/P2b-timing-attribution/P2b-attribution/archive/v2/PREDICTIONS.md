# P2b predictions, recorded before any P2b data

2026-11-08 ~23:15 UTC. The P2b sequence on the labhost is still waiting for P2 to finish; no P2b trial exists. Basis: the P2 calibration decomposition in `../../reports/P2-calibration-attribution.md`. About 60% of the separate-arm speedup happens inside Artifactory; the gateway-blocked `stats` recipe shows none; the effect exists only during the sender window.

| Arm | Prediction | Basis |
| --- | --- | --- |
| `shared` | Holdout accuracy well above 0.5; receiver median S lower when active | Replicates the P2 separate-arm calibration effect with a single recipe (`get`, the largest calibration effect: 1.24 vs 1.59 ms inside Artifactory) |
| `pergw` | Still well above 0.5, similar to `shared` | The main cause is inside the shared Artifactory, not our gateway |
| `pad` | Near 0.5 if a 12 ms floor exceeds every cached response; median probe latency rises by about 8 ms | The floor removes the receiver-visible variation |
| `aa` | Near 0.5 | Null |

If `pergw` falls to chance, the prediction is wrong, and the gateway carries more of the effect than the router durations suggest. That result will be reported as it comes.
