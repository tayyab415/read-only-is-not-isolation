# P8: host CPU counters across container boundaries

## Question
Do private cgroups, namespaces, private file stores and a CPU quota hide aggregate host CPU activity from a shell-enabled sandbox, and does a static bind over `/proc/stat` close that input?

## Setup
- Two clients (Alice, Bob), each with a private internal network, private immutable mirror, private cgroup namespace, private tmpfs and read-only root; UID/GID 20001, all capabilities dropped, no-new-privileges, 128 MiB memory, 48 PIDs, `cpu.max` 50000 100000. Native Docker 29.1.3, kernel 7.0.0-1014-aws, 4 host CPUs.
- A fixed CPU sender and a scripted reader that samples its `/proc/stat` CPU row at 22 fixed offsets from 0.1 to 2.2 s. Four cells of 64 held trials: native, masked (static file bound over `/proc/stat`), and an idle A/A control for each.
- 16 smoke, 96 calibration, 256 held trials. Decoders frozen before holdout; primary family 9,999 blocked label draws with four-test Holm. No provider calls and no extra VM.

## Result (from the study report)
| Configuration | Correct | Accuracy | Original 99% interval | Primary raw p | Holm p |
| --- | ---: | ---: | --- | ---: | ---: |
| native | 64/64 | 1.00000 | [0.920548, 1.000000] | 0.0001 | 0.0004 |
| masked | 32/64 | 0.50000 | [0.336445, 0.663555] | 1 | 1 |
| aa-native | 34/64 | 0.53125 | [0.365608, 0.692126] | 0.4039 | 1 |
| aa-masked | 32/64 | 0.50000 | [0.336445, 0.663555] | 1 | 1 |
- Native confusion matrix: TP 32, TN 32, FP 0, FN 0. Masked and A/A-masked: TP 0, TN 32, FP 0, FN 32. Exact conditional sensitivity for native: raw p 3.544704151e-13, Holm 1.417881660e-12.
- All 2,816 observer samples in the native held cells match trusted host accounting; all 2,816 in the masked cells match the bound static file. The workload still ran in masked cells (active sender CPU median 1.027396039 s native, 1.024724577 s masked).
- Private file utility held: 736 trial file tasks and 1,472 mirror GETs pass. Native audits make 92,640 checks (4,160 smoke, 24,200 calibration, 64,280 held).
- The mask removes this one live input. Other procfs/sysfs files, other mounts, scheduling and HTTP timing are untested. This is activity discrimination by a fixed reader, not message transmission. LXCFS and counter masking are existing approaches; this study only compares them on this stack.

## Files
- `P8-telemetry/`: `PROTOCOL.md`, `SOURCES-FROZEN.json`, `FROZEN.json`, `ALLOW-COLLECTION.json`, `gate-evidence/`, code (`participant.py`, `collector.py`, `telemetry.py`, `experiment.py`, `observer_contract.py`, `runtime.py`, `sequence.py`, `analysis.py`, `audit.py`), tests, `LOCAL-TESTS.txt`, `results/p8-m1/` (calibration models, holdout results), `data/p8-m1/<cell>/` (seals, provisioning, readiness, audit), `sources/provenance.json` (URLs and hashes of the ten primary sources), `bundle/MANIFEST.json`.
- `P8-verification/`: independent arithmetic (`verify.py`, `RESULT.json`, `VERIFICATION-FROZEN.json`).
- `results/`: architecture and held-result figures with generators.

## How to rerun
Run `../make-evidence-tree.sh`; `python3 -B -m unittest` passes 7 tests in `_evidence/P8-verification` and 43 of 44 in `_evidence/P8-telemetry` (one test imports P3 deployment code that is not in this release). Collection needs Linux Docker with cgroup v2 on a VM (the lab used an AWS instance). Re-running `analysis.py` or `audit.py` needs the large per-cell files, which are not included.

## Not included
18 files, 8.5 MB of source files were left out. By category: source READMEs (preparation-stage notes): 2 files; run-process notes: 1 file; tarballs and archives: 1 file, 0.2 MB; raw data files over 1 MB: 4 files, 7.9 MB; fetched third-party documents: 10 files, 0.4 MB. The fetched documents are the kernel, Moby, OCI and LXCFS source pages; URLs and SHA-256 are kept in `provenance.json`. Raw logs and data are available on request.

## Edited for release
21 files were changed in the release copies; the full list, with the original and release SHA-256 of each, is in `RELEASE-EDITS.tsv`. Absolute paths in docker bind-mount sources replaced by `$LAB_HOME` in 18 provision and readiness JSON files; the lab hostname replaced by `labhost` in PROTOCOL.md and two pre-data readbacks. 20 of these 21 are hashed in a freeze, seal or manifest file in this folder, so their release copies no longer match that hash; the original hash is in column 2 of the TSV.
