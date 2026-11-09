#!/usr/bin/env python3
"""Deliberate audit-corruption cases. Each one edits a valid synthetic episode and states the
failure code verify() must report. Run directly (python3 audit/corruption_tests.py) or via the
unit tests. A case with expected == () must still pass."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import make_synthetic as ms  # noqa: E402
from verify_episode import verify  # noqa: E402

SECRET = "hf_TESTSECRET_0123456789abcdef"


def load(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


def save(p, rows):
    Path(p).write_text("".join(json.dumps(r) + "\n" for r in rows))


def edit_rows(d, name, fn):
    rows = load(d / name)
    out = fn(rows)
    save(d / name, rows if out is None else out)


def edit_ledger(led, fn):
    rows = load(led)
    out = fn(rows)
    save(led, rows if out is None else out)


def edit_json(d, name, fn):
    obj = json.loads((d / name).read_text())
    fn(obj)
    (d / name).write_text(json.dumps(obj))


def request(rows, actor_turn, attempt=None):
    return next(r for r in rows if r.get("kind") == "request" and r["turn"] == actor_turn)


# each case: fn(d, ledger) mutates in place
def foreign_tenant_row(d, led):
    edit_rows(d, "gateway.jsonl", lambda rows: rows.append(
        {**rows[0], "rid": "evil-1", "tenant": "ten-SOMEONE-ELSE", "episode": "OTHER-EP"}))


def foreign_tenant_row_independent(d, led):
    edit_rows(d, "gateway.jsonl", lambda rows: rows.append(
        {**rows[0], "rid": "evil-2", "tenant": [r for r in rows if r["tenant"]][-1]["tenant"], "node": "nodeX", "slot": 1}))


def altered_tool_output_vs_exec(d, led):
    def f(rows):
        t = next(r for r in rows if r["kind"] == "tool" and r["turn"] == 1)
        t["exec"]["stdout"] = "tampered\n"
    edit_rows(d, "actor-A.jsonl", f)


def altered_tool_message_in_history(d, led):
    def f(rows):
        m = request(rows, 2)["request"]["messages"]
        next(x for x in m if x["role"] == "tool")["content"] += "extra"
    edit_rows(d, "actor-A.jsonl", f)


def deleted_ledger_reservation(d, led):
    edit_ledger(led, lambda rows: [r for r in rows if not (r["kind"] == "reserve" and r["call_key"].endswith("/A/1/1"))])


def deleted_ledger_actual(d, led):
    edit_ledger(led, lambda rows: [r for r in rows if not (r["kind"] == "actual" and r["call_key"].endswith("/A/2/1"))])


def duplicate_turn(d, led):
    def f(rows):
        i = next(i for i, r in enumerate(rows) if r["kind"] == "request" and r["turn"] == 2)
        rows[i:i] = [dict(rows[i]), dict(rows[i + 1])]
    edit_rows(d, "actor-A.jsonl", f)


def skip_tool_result(d, led):
    def f(rows):
        m = request(rows, 2)["request"]["messages"]
        m.remove(next(x for x in m if x["role"] == "tool"))
    edit_rows(d, "actor-A.jsonl", f)


def exec_outside_window(d, led):
    shift = 1000 * 10 ** 9
    def fa(rows):
        t = next(r for r in rows if r["kind"] == "tool" and r["turn"] == 1)
        t["exec"]["t_start_ns"] += shift
        t["exec"]["t_end_ns"] += shift
    edit_rows(d, "actor-A.jsonl", fa)
    def fe(rows):
        r = next(r for r in rows if r["container"].endswith("-1") and r["t_start_ns"] < rows[-1]["t_start_ns"])
        r["t_start_ns"] += shift
        r["t_end_ns"] += shift
    edit_rows(d, "execlog.jsonl", fe)


def execlog_row_deleted(d, led):
    edit_rows(d, "execlog.jsonl", lambda rows: rows[1:])


def execlog_extra_row(d, led):
    def f(rows):
        rows.append({**rows[0], "t_start_ns": rows[0]["t_start_ns"] + 12345})
    edit_rows(d, "execlog.jsonl", f)


def secret_in_file(d, led):
    (d / "canary-A.json").write_text(json.dumps({"ok": True, "leak": SECRET}))


def secret_in_workspace(d, led):
    (d / "workspace-B.tar.gz").write_bytes(ms.tar_bytes({"notes.txt": f"token={SECRET}\n"}))


def stop_reason_mismatch(d, led):
    edit_json(d, "episode.json", lambda o: o["stop_reasons"].update(A="turns"))


def status_stop_inconsistent(d, led):
    edit_json(d, "episode.json", lambda o: o.update(status="provider_failure"))


def usd_mismatch(d, led):
    edit_json(d, "episode.json", lambda o: o["actual_usd"].update(A=o["actual_usd"]["A"] * 2))


def tool_row_deleted(d, led):
    def f(rows):
        i = next(i for i, r in enumerate(rows) if r["kind"] == "tool" and r["turn"] == 1)
        del rows[i]
    edit_rows(d, "actor-A.jsonl", f)


def duplicate_call_key(d, led):
    def f(rows):
        rows[[i for i, r in enumerate(rows) if r["kind"] == "request"][1]]["call_key"] = rows[0]["call_key"]
    edit_rows(d, "actor-A.jsonl", f)


def dropped_gateway_row(d, led):  # tool-driven request lost from the gateway log: not required to fail
    edit_rows(d, "gateway.jsonl", lambda rows: [r for r in rows if r["rid"] != next(x["rid"] for x in rows if x["tenant"])])


def extra_401_rows(d, led):  # counted and reported, not a failure
    edit_rows(d, "gateway.jsonl", lambda rows: rows.append({**rows[0], "rid": "x401", "tenant": None, "episode": None, "status": 401}))


def canary_failed(d, led):
    (d / "canary-B.json").write_text(json.dumps({"ok": False, "probes": {"no_internet": False}}))


def tool_message_wrong_call_id(d, led):
    def f(rows):
        m = request(rows, 2)["request"]["messages"]
        next(x for x in m if x["role"] == "tool")["tool_call_id"] = "call_bogus"
    edit_rows(d, "actor-A.jsonl", f)


CASES = {
    "foreign_tenant_row": (foreign_tenant_row, ("foreign_tenant_row",), {}),
    "foreign_tenant_row_independent": (foreign_tenant_row_independent, ("node_tenant_mismatch",), {"infra": "independent"}),
    "altered_tool_output_vs_exec": (altered_tool_output_vs_exec, ("tool_visible_mismatch", "exec_hash_mismatch"), {}),
    "altered_tool_message_in_history": (altered_tool_message_in_history, ("tool_content_mismatch",), {}),
    "tool_message_wrong_call_id": (tool_message_wrong_call_id, ("history_tool_order",), {}),
    "deleted_ledger_reservation": (deleted_ledger_reservation, ("ledger_reservation_missing",), {}),
    "deleted_ledger_actual": (deleted_ledger_actual, ("ledger_actual_missing",), {}),
    "duplicate_turn": (duplicate_turn, ("duplicate_turn",), {}),
    "skip_tool_result": (skip_tool_result, ("history_missing_tool_result",), {}),
    "exec_outside_window": (exec_outside_window, ("exec_outside_window",), {}),
    "execlog_row_deleted": (execlog_row_deleted, ("execlog_missing",), {}),
    "execlog_extra_row": (execlog_extra_row, ("execlog_unmatched",), {}),
    "secret_in_file": (secret_in_file, ("secret_found",), {}),
    "secret_in_workspace_tar": (secret_in_workspace, ("secret_found",), {}),
    "stop_reason_mismatch": (stop_reason_mismatch, ("stop_reason_mismatch",), {}),
    "status_stop_inconsistent": (status_stop_inconsistent, ("status_stop_inconsistent",), {}),
    "usd_mismatch": (usd_mismatch, ("usd_mismatch",), {}),
    "tool_row_deleted": (tool_row_deleted, ("tool_row_missing",), {}),
    "duplicate_call_key": (duplicate_call_key, ("duplicate_call_key",), {}),
    "canary_failed": (canary_failed, ("canary_failed",), {}),
    "dropped_gateway_row_still_passes": (dropped_gateway_row, (), {}),
    "extra_401_rows_still_pass": (extra_401_rows, (), {}),
}


def run_case(name, root=None):
    fn, expected, kw = CASES[name]
    tmp = Path(root or tempfile.mkdtemp(prefix="bc-corrupt-"))
    led = tmp / "hf-ledger.jsonl"
    d = ms.make_episode(tmp, led, **kw)
    base = verify(d, led, secrets=[SECRET])
    assert base["ok"], f"baseline for {name} is not clean: {base['failures']}"
    fn(d, led)
    res = verify(d, led, secrets=[SECRET])
    codes = {f["code"] for f in res["failures"]}
    ok = (res["ok"] if not expected else (not res["ok"] and set(expected) <= codes))
    shutil.rmtree(tmp, ignore_errors=True)
    return {"case": name, "expected": list(expected), "codes": sorted(codes), "pass": ok}


def main():
    results = [run_case(n) for n in CASES]
    for r in results:
        print(f"{'ok  ' if r['pass'] else 'FAIL'} {r['case']}: expected {r['expected'] or 'pass'}, got {r['codes'] or 'pass'}")
    sys.exit(0 if all(r["pass"] for r in results) else 1)


if __name__ == "__main__":
    main()
