# P1 reporting rules

2026-11-08, before any main holdout file was retrieved or analysed. These reporting rules supplement the frozen version-2 analysis. They do not change feature extraction, model fitting, selected hypotheses, or the underlying tests.

## Evidence gates

1. All five configurations have exactly 300 calibration and 1,000 holdout trials. Local calibration, provisioning, source, fitted-model, and sealed holdout hashes match.
2. Every receiver request joins exactly once to its authenticated gateway request. Every forwarded request joins to backend evidence; every gateway-blocked request has no matching backend request. Missing, duplicate, or mismatched joins prevent causal claims for the affected trial. Report failures in the denominator.
3. The independent A/A and sham nulls meet the frozen protocol criterion. A significant null is a pipeline or experiment-control problem to investigate. Do not suppress a feature or change a decoder merely to silence it.
4. The baseline confirms both intended mechanisms, using the exact selected feature and request-type comparisons. Download-statistics and missing-path timing are separate validation requirements.
5. Combined and separate configurations must have the expected storage-policy responses. Unexpected residual effects are findings to investigate, not grounds to tune the meter until they disappear.

Inspect null and positive-control results before writing a verdict about residual configurations. The analysis program may produce all tables in one invocation. That invocation does not itself validate the meter.

## Multiplicity

Keep the analysis program's per-configuration outputs unchanged.

- Correct the five primary classifier permutation p-values together with Holm at 0.05. Report unadjusted values too. Binomial p-values are supplementary under their stated trial-independence assumption.
- Recompute Holm over the union of the five frozen feature-confirmation families, using their raw holdout p-values. An absent or constant comparison retains p = 1. This supplies a conservative experiment-wide feature verdict.
- Correct the union of the classifier's request-type breakdowns across configurations. These are secondary decoder results, not independent new experiments.
- Label a full holdout feature scan exploratory even if its within-configuration correction passes. New protocols, choices of thresholds, configuration selection, or hardening choices based on that scan require a fresh frozen holdout.

The protocol's null checks still use its declared nominal criterion. The global correction does not excuse a null that fails that check. With only two physical null runs, passing checks cannot establish a population false-positive rate of 5%. Report that limitation.

## Effects and information rates

A feature test demonstrates an observation difference under the sampled action distribution. It does not by itself identify a usable decoder or establish communication capacity.

For a fixed decoder on balanced trials, report accuracy, false-positive and true-positive rates, trial count, and an exact binomial interval with its independence assumption. Report the permutation test as the primary association test because the schedule was balanced and randomized.

The program's `1 - H2(error)` is a plug-in information lower bound for the defined binary protocol under the assumed distribution. It is neither a finite-sample confidence bound nor a maximum channel capacity. If reporting a conservative information value, transform an accuracy confidence lower endpoint only when that endpoint is greater than 0.5. Give the interval's family and confidence level.

For a confirmed new protocol, a later experiment must record the sender schedule, receiver decoder, seconds per symbol, repetitions or coding scheme, verified decoded symbols, and uncertainty. Convert that protocol's measured information per symbol to information per hour. Do not infer a service-wide maximum.

## Negative results

For each negative result, name the sampled sender requests and their counts, receiver battery, settle gap, workload, observation duration, decoder, and statistical power.

For the pooled classifier with 1,000 balanced trials, calculate power at the actual corrected critical value, rather than repeating an uncorrected three-standard-deviation approximation. For request-type results, use their actual active-trial counts. State the effect model used in each power calculation.

A serial batch design and the fixed configuration order limit causal comparison across configurations. Within-run randomized sender activity supports a narrower contrast. Shared host, service, and controller resources can still explain a response difference; attribution needs controlled interventions and independent logs.

The battery explicitly includes storage-statistics endpoints and trial-specific missing paths because earlier experiments identified them. Generic feature selection receives no field-level decoder hints, but the battery is informed by that prior knowledge. Describe it as a generic analysis of an informed probe battery, not an entirely unguided channel discovery.
