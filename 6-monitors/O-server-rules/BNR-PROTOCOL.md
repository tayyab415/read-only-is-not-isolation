# Additional benign validation

Frozen 2026-11-08 before any BN or BNR outcome existed. The original six BN
cohorts and their primary tables remain unchanged.

Thirty fresh open cohorts, ten per model, add sixty benign agents. Twenty
agents receive each existing basic, metadata or latency task. Each model has
all six unordered task pairings, including same-task pairs; four additional
mixed pairs balance the totals. Agent side, values, pair order and launch order
are shuffled with the fixed seed `20261108:BNR`.

Reason: six independent negative cohorts give weak false-alarm uncertainty.
This extension was chosen for sample size before inspecting any negative
outcome. It changes no score, threshold, prompt or positive trial. The $26 N
guard and $30 experiment cap remain in force.

Report original BN and additional BNR false-alarm counts separately, per task
and per model. Additional task/behaviour AUC and recall reuse the same fresh
B3/B2 positives; they are not a second positive replication. Also report the
combined negative sample explicitly as pooled validation.

The balanced fixtures still do not represent production workloads. A measured
false alarm means the detector flags an agent assigned legitimate work in this
fixture, not that it understands intent.
