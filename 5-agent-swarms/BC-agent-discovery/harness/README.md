# BC harness

Controller for the B and C episodes. Python 3.11, standard library plus `requests`.
Flat modules; run everything from `BC/`.

## Run

```
python3 -m unittest discover -s harness/tests -v     # no network, no ssh

python3 harness/fleet.py --schedule tasks/schedule.json --nodes node/nodes.json \
    --out data --max-parallel 3 [--stop-at-utc 2026-11-09T09:00:00Z] \
    [--only-blocks C1 B1] [--retry-failed] [--cap 13.0] [--episode-max-usd 0.10]
python3 harness/fleet.py ... --fake      # FakeNode + FakeModel + stub prompts/fixtures
```

`--fake` writes its ledger to `<out>/fake-ledger.jsonl`. The real run uses
`ledger/hf-ledger.jsonl`, loads the HF token with `library/retrieve.py:credential()`, and
scans `--out` for the token string when it ends.

Stop new launches: `touch data/STOP`. In-flight episodes finish. Ctrl-C does the same.

## Modules

| file | what |
| --- | --- |
| `render.py` | `render_tool_result(exec_json)`, `clip()`. Pure. The audit imports it. |
| `ledger.py` | `Ledger(path, cap)`: `reserve`, `actual`, `total`. Threading lock + `fcntl` lock file. `reservation_usd`, `actual_usd`, `PRICES`. |
| `models.py` | `HFClient.call(model_key, messages, tools, meta) -> (response, attempts)`, `FakeModel`, `FakeFail`, `ProviderFailure`, `BudgetExceeded`, `load_token`, `assert_no_token`. |
| `node.py` | `SSHNode(alias)`, `FakeNode`, `InfraFailure`. Same eight commands as `node_ctl.sh`. |
| `episode.py` | `run_episode(...)`, `resolve_actors(row)`. |
| `fleet.py` | `run_fleet(...)` and the CLI. |
| `tests/` | unittest files plus `stub_prompts.py` and `stub_fixtures.py`. |

## Interfaces to the other builders

**Prompts** (`tasks/prompts.py`): `build(study, role, condition, private_info, model_key,
t0_utc_iso, limits) -> (system_text, user_text, tools_list)`. `condition` is
`cell.workload` (C) or `cell.prompt` (B). `limits` is
`{"turns":12,"wall_s":240,"bash_timeout_s":{"min":1,"max":60,"default":30}}`.
The harness appends ` /no_think` to the Qwen system text if it is not already there.

**Fixtures** (`tasks/make_fixtures.py`): a callable, or a module with `make_workspace`
(also tried: `build_workspace`, `make_fixture`, `make`), called as
`fn(spec_row, actor, role) -> (tar_gz_bytes, private_info_dict)`. `actor` is `"A"`/`"B"`.

**Checker**: `run_episode(..., checker=fn)` or a `check` attribute on the fixtures module,
called as `fn(spec_row, {actor: {workspace_tar, private_info, finish_args, stop_reason}})`
and its dict is written to `check.json`. Without one, `check.json` says `checked: false`.

**Schedule row**: `episode_id, study, block, cell{pairing, workload|prompt, infra}, roles`,
plus any other fields (passed through to `spec.json` and the fixtures). `roles` maps role
name to model key: `{"A":"qwen","B":"llama"}` for C, `{"sender":"qwen","receiver":"llama"}`
for B. Sender is actor A. If `roles` is missing, `cell.pairing` ("qwen-llama") gives A, B.
A file can be a list of rows or `{"episodes": [...]}`.

**nodes.json**: `[{"name":"n01","ssh":"bc-n01"}]`.

## Episode directory

`data/<study>/<episode_id>/` as in SPEC. Details:

- `actor-<A|B>.jsonl` rows: `request` and `response` per HTTP attempt (both carry `call_key`
  and `attempt`; `response` has `http_status`, `response`, optional `error`), `tool`
  (`arguments` is the raw string the model sent; `exec` is the node JSON plus `timeout_s`,
  the clipped timeout; `visible` is the string the model saw), and one final `stop`
  (`reason`, `finish_args`, optional `detail`).
- `gateway.jsonl`, `execlog.jsonl`: node rows, each tagged with `node`.
- `episode.json` is written last, atomically. Its existence means the episode is finished.
  Fields: `status`, `t0_ns`, `t0_utc`, `t_end_ns`, `stop_reasons`, `call_counts`,
  `reserved_usd`, `actual_usd` (per actor and total), `ledger_total_usd`, `canary_ok`,
  `teardown`, `teardown_ok`, `workspace_sha256` (node's value and the controller's), `errors`.
- Status: `infra_failure` if any actor hit one, a canary failed, a collect/log call failed,
  or teardown failed; else `budget_stop`; else `provider_failure`; else `complete`.

Fleet progress: `data/progress.jsonl` (`launch`, `done`, `skip`, `stop_launching`,
`node_quarantined`, `moved_aside`, `fleet_end`).

## Resume

An episode with `episode.json` is skipped, whatever its status. `--retry-failed` re-runs
the ones that are not `complete` (old dir renamed `<id>.retry-<ns>`). A directory without
`episode.json` (crash) is renamed `<id>.partial-<ns>` and the episode re-runs. Analysis
code should ignore directories with a `.retry-` or `.partial-` suffix.

## Interpretations

- `run_episode`'s `out_dir` is the data root; the episode goes in `<out_dir>/<study>/<id>`.
- Controller messages: the time-up message is sent once, before the first request that has
  fewer than 30 s or fewer than 2 turns left (so always before turn 12). It replaces the
  continue message on that request. After a response with no tool call the next request
  gets the continue message, also after time-up was already sent.
- `[timed out after <t>s]` uses the clipped timeout the harness passed (`exec.timeout_s`).
  Without that key it falls back to the measured duration.
- The 4000-character rule applies to the whole rendered text (header, STDOUT and STDERR).
- Each HTTP attempt gets its own `call_key` `<episode>/<actor>/<turn>/<attempt>` and its own
  reservation. A failed attempt keeps its reservation. If a 200 response has no `usage`,
  its reservation stands as the cost.
- Retries are also used for a 200 response that is not JSON or has no `choices`. After the
  third retry fails the actor stops with `provider_failure`.
- `budget_stop` is an actor stop reason (SPEC lists it only as an episode status).
- A failed canary means no actor starts. The episode is `infra_failure` and is still
  collected and torn down.
- `finish` always ends that actor; unparsable arguments are stored as `{"_unparsed": raw}`.
  Tool calls after `finish` in the same response are not run.
- Tools other than `bash`/`finish`, or bad `bash` arguments, return a `[error] ...` tool
  message and the actor continues.
- `bash` `timeout_s` is clamped to 1..60 (default 30), then to the remaining wall (at least 1 s).
- The model call in flight is not interrupted at 240 s; the wall is checked before each call
  and after each response (tools are not run after the wall).
- gwlog/execlog are queried on every node the episode used, window t0-2 s to end+2 s.
- Fleet: schedule order is strict. A row that needs two free nodes blocks later rows.
  The launch guard is `ledger total + (in-flight + 1) * episode_max_usd > cap`; the default
  $0.10 per episode is a guess, set `--episode-max-usd` after the first real episodes.
  A node with a failed teardown, or 2 consecutive `infra_failure` episodes, is quarantined for
  the rest of the run. Preflight calls `status` on each node and drops any with `vllm_ok` false.
- `--stop-at-utc` stops launching mid-block. SPEC says a time stop keeps whole blocks, so the
  analysis must drop incomplete blocks; the harness does not.

## Known limits

- `SSHNode` is tested only against a stub `ssh` on PATH, never a real node.
- `HFClient` is tested only with a stubbed `requests.post`. No real HF call has been made.
- `FakeNode` has no isolation, gateway, or docker; its `gwlog` is always empty.
- Reservations use SPEC's bytes/2 token estimate. Long histories raise it; watch `actual_usd`.
- No integration with the real `tasks/` or `node_ctl.sh` yet; names and call shapes above
  are the assumed contract.
