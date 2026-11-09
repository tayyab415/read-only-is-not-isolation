
### In-distribution test (held-out Swarm Traces clusters vs held-out training-source benign). AUC as fraction, rates in %

| result | model | test_auc | test_tpr@thr1 | test_fpr@thr1 | test_tpr@thr01 |
|---|---|---|---|---|---|
| gate_v1 | tfidf:raw | 100.0 ± 0.0 | 100.0 ± 0.0 | 1.4 ± 0.8 | 100.0 ± 0.0 |
| gate_v1 | tfidf:detok | 100.0 ± 0.0 | 100.0 ± 0.0 | 1.3 ± 0.5 | 99.9 ± 0.1 |
| gate_v1 | tfidf:struct | 100.0 ± 0.0 | 99.8 ± 0.1 | 1.6 ± 0.7 | 97.1 ± 2.8 |
| gate_v1 | probe:style | 99.1 ± 0.1 | 95.5 ± 0.4 | 1.0 ± 0.3 | 22.9 ± 25.5 |
| gate_v1 | probe:tokens | 97.6 ± 0.2 | 95.2 ± 0.3 | 0.7 ± 0.2 | 69.5 ± 3.5 |

### False-positive rate (%) on benign sources never seen in training, at the 1%-val-FPR threshold

| result | model | B_agentapprove | B_glaive_calls | B_hf_docs_api | B_rogue_safe | B_swegym_bash | B_tomng_allow |
|---|---|---|---|---|---|---|---|
| gate_v1 | tfidf:raw | 17.7 ± 2.0 | 3.3 ± 0.0 | 20.7 ± 0.9 | 38.6 ± 7.6 | 1.4 ± 0.4 | 31.0 ± 4.1 |
| gate_v1 | tfidf:detok | 20.0 ± 2.2 | 3.0 ± 0.6 | 20.8 ± 0.3 | 56.1 ± 1.5 | 1.5 ± 0.5 | 38.2 ± 4.2 |
| gate_v1 | tfidf:struct | 33.3 ± 5.7 | 70.0 ± 8.8 | 10.1 ± 1.1 | 93.9 ± 6.1 | 0.1 ± 0.1 | 6.5 ± 1.3 |
| gate_v1 | probe:style | 7.8 ± 0.8 | 1.1 ± 0.0 | 3.6 ± 0.2 | 9.6 ± 1.5 | 0.2 ± 0.0 | 1.7 ± 0.0 |
| gate_v1 | probe:tokens | 2.4 ± 0.0 | 0.0 ± 0.0 | 0.3 ± 0.0 | 5.3 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 |

### Detection rate (%) on attacks from other sources, at the same threshold

| result | model | A_agentapprove | A_revshell | A_rogue_unsafe | A_tomng_deny |
|---|---|---|---|---|---|
| gate_v1 | tfidf:raw | 33.2 ± 3.0 | 99.0 ± 0.0 | 46.9 ± 4.3 | 60.6 ± 4.4 |
| gate_v1 | tfidf:detok | 38.9 ± 2.6 | 99.0 ± 0.0 | 79.0 ± 4.3 | 68.9 ± 5.1 |
| gate_v1 | tfidf:struct | 29.0 ± 3.9 | 69.4 ± 8.1 | 96.3 ± 0.0 | 9.2 ± 1.3 |
| gate_v1 | probe:style | 6.3 ± 0.9 | 85.7 ± 0.0 | 30.9 ± 4.3 | 2.2 ± 0.0 |
| gate_v1 | probe:tokens | 1.7 ± 0.0 | 0.0 ± 0.0 | 14.8 ± 0.0 | 0.0 ± 0.0 |

### Paired AUC within one outside dataset (its attacks vs its own benign rows). 0.5 = chance

| result | model | agentapprove | tomngdev | rogue |
|---|---|---|---|---|
| gate_v1 | tfidf:raw | 0.682 ± 0.003 | 0.692 ± 0.003 | 0.656 ± 0.005 |
| gate_v1 | tfidf:detok | 0.691 ± 0.003 | 0.701 ± 0.006 | 0.682 ± 0.005 |
| gate_v1 | tfidf:struct | 0.454 ± 0.004 | 0.599 ± 0.008 | 0.621 ± 0.079 |
| gate_v1 | probe:style | 0.622 ± 0.004 | 0.608 ± 0.003 | 0.652 ± 0.002 |
| gate_v1 | probe:tokens | 0.364 ± 0.000 | 0.309 ± 0.000 | 0.535 ± 0.000 |
