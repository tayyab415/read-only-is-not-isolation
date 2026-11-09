# T2: does opaque artifact naming remove the shared code?

## Question

In T1, a package mirror with files `pkg-00.bin` to `pkg-15.bin` gave 41% fully compatible mixed-developer pairs, because the names line up with the values 0 to 15. If the files get opaque names, does that agreement drop?

## Setup

- Same seven models, system prompt, wording (W1 only), roles, parser and scorer as T1. The T1 files are imported unchanged. Frozen on 2026-11-09 before main collection.
- Two contexts collected together in one random order. C1 is the T1 mirror, rerun as a replication. C2 is the same mirror with 16 opaque six-character file names (`s3ekiw.bin`, `t655ot.bin` and so on) that the server lists in a fixed order.
- 24 calls per model, role and context, 672 calls, all completed. Two C1 receiver outputs did not parse and count as matching nothing.
- Disclosure from `PROTOCOL.md`. A 28-call smoke run came first, and it showed most models mapping value n to the n-th listed file. The hypotheses were written after seeing it, so T2 tests the size of the change, not a blind prediction. Smoke outputs are in `smoke/`.
- Rule fixed in advance. If opaque naming does not lower different-developer agreement by at least 0.10 with an interval excluding 0, it is not supported as a control on the shared code.

## Result

Full 16/16 compatibility, same wording, 95% stratified two-way bootstrap (10,000 resamples):

| Pair class | C1, value-aligned names | C2, opaque listed names | C2 minus C1 |
| --- | --- | --- | --- |
| Different developer (40 ordered pairs) | 0.392 (0.348 to 0.437) | 0.559 (0.518 to 0.600) | +0.167 (0.106 to 0.228) |
| Same model (7) | 0.587 (0.540 to 0.636) | 0.707 (0.671 to 0.744) | +0.120 (0.059 to 0.179) |
| Same developer (Luna and gpt-oss) | 1.000 | 1.000 | 0 |

Opaque naming raised agreement, so it is not supported as a control. In C2, senders signalled value n with the n-th file in the server's listing. Counts of 24 sender tables per model, classified by the path for value 7:

| Model | n-th listed file | n-th file in sorted order | Other |
| --- | ---: | ---: | ---: |
| Luna | 24 | 0 | 0 |
| gpt-oss-120b | 24 | 0 | 0 |
| Qwen3 Next | 24 | 0 | 0 |
| GLM 5 | 23 | 1 | 0 |
| Mistral Large 3 | 23 | 0 | 1 |
| DeepSeek v3.2 | 16 | 1 | 7 |
| Llama 4 Maverick | 9 | 6 | 9 |

Any ordered structure the server exposes became the code. The C1 rerun matches T1 descriptively (different developer 0.39 here against 0.41 in T1's pooled C1 cells; same model 0.59 against 0.60). Hiding listings entirely was not tested. On T1's bare server, cross-developer agreement was lower but still 0.23.

Limits. Seven models, one temperature, one task family, one opaque name list and one listing order. Models were not told their partner's model. Cost was $0.51 including smoke.

## Files

- `PROTOCOL.md`, `FREEZE.json`: design and hashes (`collect_t2.py`, `analyze_t2.py`, the protocol, and the T1 `prompts.py`, `score.py`, `collect.py`).
- `collect_t2.py`: collection (paid Bedrock calls). `analyze_t2.py`: scoring and bootstrap.
- `main/calls.jsonl`: the 672 raw model outputs (3.9 MB). `main/analysis.json`: the frozen analysis. `main-collect.log`. `smoke/calls.jsonl`: the 28-call smoke.

## How to rerun

`analyze_t2.py` reads `main/calls.jsonl` and imports the T1 code from a sibling folder named `T-twins`. Build the layout in `../README.md` (T1 folder copied as `T-twins`), then run `python3 analyze_t2.py`. `collect_t2.py` needs AWS Bedrock access and about $0.50 of model calls.

## Not included

- `budget-ledger.jsonl` (0.25 MB). Raw logs available on request.
- Source total 9 files, 4.3 MB.

## Edited for release

None.
