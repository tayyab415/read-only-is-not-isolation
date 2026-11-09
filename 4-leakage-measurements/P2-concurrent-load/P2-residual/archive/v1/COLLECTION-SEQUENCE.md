# P2 launch sequence

Preparation only. No collection permission record exists yet. The code checks it before provisioning.

1. Finish and validate P1. Run its independent audit and corrected reporting. Inspect both physical nulls and both known mechanisms. Record exact feature comparisons and causal source locators in `gate-evidence/known-channel-validation.json`; do not use calibration significance as validation.
2. Preserve all local and remote P1 data. Copy the six public gate artifacts: main `FROZEN.json`, `REPORTING-FROZEN.json`, `audit-results.json`, `report-metrics.json`, `holdout-results.json`, and `known-channel-validation.json`. Retain their hashes in `ALLOW-COLLECTION.json`, together with the P2 source-freeze hash and explicit gate decisions. A failed P1 gate needs investigation and an amendment, not an invented passing decision.
3. Transfer only the source bundle, public gate records, and frozen package manifest/wheels to `~/workspaces/artifactory-lab/next/`. Keep the directory names `P1-meter`, `P2-residual`, `P3-mirror`, and `P3-isolation`. P1's frozen `service.py` deliberately continues to use its original private runtime directory, `~/workspaces/artifactory-lab/p1`. Never transfer credentials, model/AWS keys, unrelated transcripts, or bulk data.
4. Verify remote source and package hashes. Do a bounded eight-trial implementation smoke per configuration with `collect.py calibration CONFIG --smoke`; audit all four. This is a separate implementation dataset. Stop and retain any failed preparation or trial. No main result can be derived from it.
5. Under one named tmux session, run `collect.py calibration CONFIG` sequentially for `separate`, `sham`, `aa`, and `immutable`. This collects 300 trials each. The common collection lock prevents overlap. Each run saves client, trusted service, rotated backend, boundary, and cleanup evidence.
6. Retrieve only calibration artifacts. Verify all seals, then run `audit.py DATA calibration`. Fit all four calibrations with `analysis.py calibrate DATA OUT`, then `analysis.py freeze DATA OUT`. The analysis refuses fitting when holdout data exists.
7. Transfer the exact model freeze and its public hashed inputs back to the controller, without credentials. Run `collect.py holdout CONFIG` sequentially for all four. This collects 1,000 trials each and requires the model freeze.
8. Retrieve the complete sealed holdout evidence. Run `audit.py DATA holdout` before `analysis.py holdout DATA OUT`.
9. Interpret A/A first. The concurrent sham can reveal shared-host effects and is not a zero-leak null. Any positive candidate requires a fresh targeted sender protocol, decoder, and controlled intervention. Do not call association a maximum channel capacity.
10. Replay the frozen dependency workload in isolated Linux clients. Utility preparation on the Mac does not replace this comparison.

Use Python 3.12 standard-library-only code on the labhost for collection. Use `python3` on the Mac for analysis, with `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, and `PYTHONDONTWRITEBYTECODE=1`.

The code keeps server runtime log space private, captures container/image/network settings, and removes only owned `p1-p2-*` runtime resources. The shared Artifactory instance and other workstreams stay running. Fixture repositories, source, private credential files, and evidence remain for inspection.

The immutable time probes use equal fixture bytes; its catalog utility uses real admitted wheels. The dummy matches retained bytes, status, and sequential delay profiles only. Those qualifications must appear in the report.

No paid subject call belongs in this launch sequence. The separate cost guard allows a later bounded subject stage only after its scientific and evidence gates pass.
