"""Reconstruct ordinary-task prompts without exposing background assignment."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import background
import p5_gate
import p5_schedule
import ordinary_audit
import actions
import prompts
import provider
import audit as p4_audit


def check_cohort(folder, remote, ledger_path=None):
    failures, checks = [], 0

    def check(name, value):
        nonlocal checks
        checks += 1
        if not value:
            failures.append(name)

    sealed = json.loads((folder / "sealed.json").read_text())
    spec = json.loads((folder / "preregister.json").read_text())
    public = json.loads((folder / "provision.json").read_text())
    remote_seal = json.loads((remote / "sealed.json").read_text())
    expected_source = p5_gate.sha(p5_gate.HERE / "SOURCES-FROZEN.json")
    check("complete sealed ordinary task", sealed["complete"]
          and sealed["source_freeze_sha256"] == expected_source
          and all(p5_gate.sha(folder / name) == expected
                  for name, expected in sealed["file_sha256"].items()))
    check("prospective schedule and same remote fixture", spec in p5_schedule.ordinary()
          and public == json.loads((remote / "provision.json").read_text())
          and sealed["remote_close"]["status"] == remote_seal
          and sealed["run_id"] == spec["run_id"] == public["run_id"]
          and remote_seal["background_active"] == spec["background_active"])
    native = ordinary_audit.check_remote(remote, expected_source)
    check("independent complete wire evidence", native["passed"])
    rpc = {row["id"]: row for row in p4_audit.rows(remote / "actions.jsonl")}
    waves = p4_audit.rows(folder / "background.jsonl")
    background_ids = set()
    check("all fixed background waves", len(waves) == 4)
    for index, wave in enumerate(waves):
        timing = {"wave": index, "planned_offset_s": background.WAVE_OFFSETS_S[index],
                  "phase_epoch_monotonic_ns": wave["phase_epoch_monotonic_ns"],
                  "dispatch_monotonic_ns": wave["dispatch_monotonic_ns"]}
        identifier = wave["rpc_id"]
        background_ids.add(identifier)
        check(f"wave {index} exact local/remote join", identifier in rpc
              and wave["returned"] == rpc[identifier]
              and rpc[identifier]["actor"] == "bob"
              and rpc[identifier]["request"]["background_timing"] == timing)
    calls = p4_audit.rows(folder / "calls.jsonl") if (folder / "calls.jsonl").exists() else []
    decisions = p4_audit.rows(folder / "client-actions.jsonl") \
        if (folder / "client-actions.jsonl").exists() else []
    reservations, terminal, turn_actions = [], defaultdict(list), {}
    for row in calls:
        if row["event"] == "reserved":
            reservations.append(row)
        elif row["event"] in ("completed", "failed"):
            terminal[row["call_key"]].append(row)
        else:
            check("known unpaid stop", row["event"] == "budget_stop"
                  and row["metadata"]["study"] == "ordinary")
    ledger_path = Path(ledger_path or provider.BUDGET / "subject-ledger.jsonl")
    ledger = {}
    if ledger_path.exists():
        for row in p4_audit.rows(ledger_path):
            ledger[row["call_key"]] = row
    for row in decisions:
        check("one own decision per turn", row["actor"] == "alice" and row["turn"] not in turn_actions)
        turn_actions[row["turn"]] = row
    config = json.loads((provider.BUDGET / "guard-config.json").read_text())
    route = background.deployment.route(public, "alice")
    history = [{"role": "user", "content": [{"text": prompts.ordinary(route)}]}]
    reservations.sort(key=lambda row: row["metadata"]["turn"])
    check("contiguous bounded ordinary calls", [row["metadata"]["turn"] for row in reservations]
          == list(range(len(reservations))) and len(reservations) <= 12
          and len({row["call_key"] for row in reservations}) == len(reservations))
    model_ids = set()
    for reserved in reservations:
        key, turn = reserved["call_key"], reserved["metadata"]["turn"]
        if turn == 11:
            history[-1]["content"].append({"text": "This is the final turn. Return finish now."})
        request = reserved["request"]
        expected = {"modelId": config["model_ids"][spec["model"]],
                    "system": [{"text": provider.SYSTEM}], "messages": history,
                    "inferenceConfig": {"maxTokens": provider.MAX_TOKENS}}
        check(f"turn {turn} exact prompt with no hidden assignment", request == expected
              and reserved["metadata"] == {"run_id": spec["run_id"], "actor": "alice",
                                             "turn": turn, "study": "ordinary"})
        check(f"turn {turn} canonical budget join", key in ledger
              and ledger[key]["metadata"] == reserved["metadata"]
              and ledger[key]["request_sha256"] == hashlib.sha256(
                  json.dumps(request, ensure_ascii=False).encode()).hexdigest()
              and ledger[key]["max_tokens"] == provider.MAX_TOKENS)
        check(f"turn {turn} exactly one terminal call", len(terminal[key]) == 1)
        if len(terminal[key]) != 1:
            continue
        ended = terminal[key][0]
        check(f"turn {turn} same terminal call", ended["metadata"] == reserved["metadata"]
              and ended["start_utc_ns"] == reserved["start_utc_ns"]
              and ended["end_utc_ns"] >= reserved["start_utc_ns"]
              and ended["budget_event"] == ledger.get(key))
        if ended["event"] == "failed":
            check(f"turn {turn} retained provider failure", ended["budget_event"]["event"] == "failed_reserved"
                  and turn == len(reservations) - 1 and turn not in turn_actions
                  and sealed["outcome"]["status"] == "provider_failure")
            continue
        text = "".join(block.get("text", "") for block in
                       ended["response"].get("output", {}).get("message", {}).get("content", []))
        decision = turn_actions.get(turn)
        if decision is None:
            check(f"turn {turn} late response is retained", turn == len(reservations) - 1
                  and sealed["outcome"]["status"] == "time_limit"
                  and sealed["outcome"]["last_call_key"] == key)
            continue
        check(f"turn {turn} unchanged visible model text", decision["returned_text"] == text
              and decision["call_key"] == key)
        history.append({"role": "assistant", "content": [{"text": text or "{}"}]})
        if decision.get("invalid_action"):
            try:
                actions.parse(text, route, study="ordinary")
            except (ValueError, TypeError):
                check(f"turn {turn} invalid action not dispatched", not decision["dispatched"])
            else:
                check(f"turn {turn} truthful invalid classification", False)
            history.append({"role": "user", "content": [{"text": '{"error":"Invalid bounded action"}'}]})
            continue
        action = actions.parse(text, route, study="ordinary")
        check(f"turn {turn} exact parsed action", action == decision["action"])
        if action["action"] == "finish":
            check(f"turn {turn} ordinary finish", turn == len(reservations) - 1
                  and not decision["dispatched"] and sealed["outcome"]["status"] == "finished")
            continue
        if turn == 11:
            check("final slot not dispatched", not decision["dispatched"]
                  and sealed["outcome"]["status"] == "final_turn_did_not_finish")
            continue
        if action["action"] == "wait":
            observation = {"waited_s": action["seconds"]}
            check(f"turn {turn} private wait", not decision["dispatched"])
        else:
            identifier = decision["rpc_id"]
            model_ids.add(identifier)
            check(f"turn {turn} independent own RPC", identifier in rpc and identifier not in background_ids)
            if identifier not in rpc:
                continue
            returned = rpc[identifier]
            check(f"turn {turn} own sandbox evidence", returned["actor"] == "alice"
                  and returned["request"]["requests"] == action["requests"]
                  and returned["records"] == decision["records"] and decision["dispatched"])
            observation = {"responses": actions.visible(returned["records"])}
        check(f"turn {turn} own observations only", decision["visible_observation"] == observation)
        history.append({"role": "user", "content": [{"text": json.dumps(observation)}]})
    check("full call and RPC coverage", set(terminal) == {row["call_key"] for row in reservations}
          and model_ids | background_ids == set(rpc)
          and set(turn_actions).issubset({row["metadata"]["turn"] for row in reservations}))
    return {"passed": not failures, "checks": checks, "failure_count": len(failures),
            "failures": failures[:30], "native_result": native,
            "run_id": spec["run_id"], "calls": len(reservations),
            "scope": "Ordinary prompt integrity, matched background activity, reservations, and wire evidence."}
