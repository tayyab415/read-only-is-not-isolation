v1-modernbert-detok
v1-modernbert-struct
### In-distribution test (held-out Swarm Traces clusters vs held-out training-source benign). AUC and rates in %
| result | model | test_auc | test_tpr@thr1 | test_fpr@thr1 | test_tpr@thr01 |
| gate_v1 | tfidf:detok | 100.0 ± 0.0 | 100.0 ± 0.0 | 1.3 ± 0.5 | 99.9 ± 0.1 |
| gate_v1 | tfidf:struct | 100.0 ± 0.0 | 99.8 ± 0.1 | 1.6 ± 0.7 | 97.1 ± 2.8 |
| gate_v1 | probe:style | 99.1 ± 0.1 | 95.5 ± 0.4 | 1.0 ± 0.3 | 22.9 ± 25.5 |
| encoder_v1 | encoder:v1-modernbert-detok | 100.0 ± 0.0 | 100.0 ± 0.0 | 0.6 ± 0.4 | 100.0 ± 0.1 |
| encoder_v1 | encoder:v1-modernbert-struct | 100.0 ± 0.0 | 99.9 ± 0.0 | 1.3 ± 0.8 | 99.6 ± 0.2 |
### False-positive rate (%) on benign sources never seen in training, at the 1%-val-FPR threshold
| result | model | B_agentapprove | B_glaive_calls | B_hf_docs_api | B_rogue_safe | B_swegym_bash | B_tomng_allow |
| gate_v1 | tfidf:detok | 20.0 ± 2.2 | 3.0 ± 0.6 | 20.8 ± 0.3 | 56.1 ± 1.5 | 1.5 ± 0.5 | 38.2 ± 4.2 |
| gate_v1 | tfidf:struct | 33.3 ± 5.7 | 70.0 ± 8.8 | 10.1 ± 1.1 | 93.9 ± 6.1 | 0.1 ± 0.1 | 6.5 ± 1.3 |
| gate_v1 | probe:style | 7.8 ± 0.8 | 1.1 ± 0.0 | 3.6 ± 0.2 | 9.6 ± 1.5 | 0.2 ± 0.0 | 1.7 ± 0.0 |
| encoder_v1 | encoder:v1-modernbert-detok | 23.8 ± 10.3 | 2.6 ± 2.6 | 10.1 ± 4.1 | 61.4 ± 7.6 | 0.6 ± 0.6 | 16.0 ± 8.9 |
| encoder_v1 | encoder:v1-modernbert-struct | 32.6 ± 12.5 | 90.0 ± 9.7 | 19.0 ± 3.9 | 98.2 ± 3.0 | 1.8 ± 1.2 | 22.5 ± 6.9 |
### Detection rate (%) on attacks from other sources, at the same threshold
| result | model | A_agentapprove | A_revshell | A_rogue_unsafe | A_tomng_deny |
| gate_v1 | tfidf:detok | 38.9 ± 2.6 | 99.0 ± 0.0 | 79.0 ± 4.3 | 68.9 ± 5.1 |
| gate_v1 | tfidf:struct | 29.0 ± 3.9 | 69.4 ± 8.1 | 96.3 ± 0.0 | 9.2 ± 1.3 |
| gate_v1 | probe:style | 6.3 ± 0.9 | 85.7 ± 0.0 | 30.9 ± 4.3 | 2.2 ± 0.0 |
| encoder_v1 | encoder:v1-modernbert-detok | 36.1 ± 3.8 | 91.5 ± 2.9 | 61.7 ± 5.7 | 35.7 ± 9.4 |
| encoder_v1 | encoder:v1-modernbert-struct | 43.1 ± 12.8 | 82.3 ± 11.2 | 98.8 ± 2.1 | 26.9 ± 12.9 |
### Paired AUC within one outside dataset (its attacks vs its own benign rows). 0.5 = chance
| result | model | agentapprove | tomngdev | rogue |
| gate_v1 | tfidf:detok | 0.691 ± 0.003 | 0.701 ± 0.006 | 0.682 ± 0.005 |
| gate_v1 | tfidf:struct | 0.454 ± 0.004 | 0.599 ± 0.008 | 0.621 ± 0.079 |
| gate_v1 | probe:style | 0.622 ± 0.004 | 0.608 ± 0.003 | 0.652 ± 0.002 |
| encoder_v1 | encoder:v1-modernbert-detok | 0.576 ± 0.052 | 0.651 ± 0.026 | 0.520 ± 0.094 |
| encoder_v1 | encoder:v1-modernbert-struct | 0.581 ± 0.055 | 0.607 ± 0.038 | 0.570 ± 0.058 |
