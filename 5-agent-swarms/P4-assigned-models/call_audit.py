"""Reconstruct each subject's visible history and join calls to confined actions."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import actions
import audit
import gate
import prompts
import provider
import schedule


def check_cohort(folder, remote, ledger_path=None):
    failures, checks = [], 0

    def check(name, condition):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)

    sealed = json.loads((folder / "sealed.json").read_text())
    spec = json.loads((folder / "preregister.json").read_text())
    public = json.loads((folder / "provision.json").read_text())
    remote_public = json.loads((remote / "provision.json").read_text())
    check("complete local seal", sealed["complete"]
          and sealed["source_freeze_sha256"] == gate.sha(gate.HERE / "SOURCES-FROZEN.json"))
    check("local seal hashes", all(gate.sha(folder / name) == expected
          for name, expected in sealed["file_sha256"].items()))
    check("assigned schedule and route readback", spec in schedule.assigned() and public == remote_public
          and sealed["run_id"] == spec["run_id"] == public["run_id"])
    checked_remote = audit.audit_remote(remote, gate.sha(gate.HERE / "SOURCES-FROZEN.json"))
    check("complete independent action audit", checked_remote["passed"])
    check("remote close matches retrieved seal", sealed["remote_close"]["status"]
          == json.loads((remote / "sealed.json").read_text()))
    calls = audit.rows(folder / "calls.jsonl") if (folder / "calls.jsonl").exists() else []
    decisions = audit.rows(folder / "client-actions.jsonl") \
        if (folder / "client-actions.jsonl").exists() else []
    rpc = {row["id"]: row for row in audit.rows(remote / "actions.jsonl")} \
        if (remote / "actions.jsonl").exists() else {}
    reservations, terminal, by_actor, action_by_turn = {}, defaultdict(list), defaultdict(list), {}
    for row in calls:
        if row["event"] == "reserved":
            key = row["call_key"]
            check("unique reservation", key not in reservations)
            reservations[key] = row
            by_actor[row["metadata"]["actor"]].append(row)
        elif row["event"] in ("completed", "failed"):
            terminal[row["call_key"]].append(row)
        elif row["event"] == "budget_stop":
            check("budget stop has recorded actor", row["metadata"]["actor"] in ("alice", "bob"))
        else:
            check("known call event", False)
    ledger = {}
    ledger_path = ledger_path or provider.BUDGET / "subject-ledger.jsonl"
    if Path(ledger_path).exists():
        for row in audit.rows(ledger_path):
            ledger[row["call_key"]] = row
    for decision in decisions:
        index = (decision["actor"], decision["turn"])
        check("one action decision per turn", index not in action_by_turn)
        action_by_turn[index] = decision
    rpc_ids = set()
    model_ids = json.loads((provider.BUDGET / "guard-config.json").read_text())["model_ids"]
    for actor in ("alice", "bob"):
        peer = "bob" if actor == "alice" else "alice"
        immutable = public.get("kind") == "immutable"
        repo = public["repositories"].get(actor) or public["repositories"].get("shared")
        route = {"kind": "immutable" if immutable else "mutable", "repository": repo,
                 "object_root": "/fixture" if immutable else f"/{repo}",
                 "metadata_root": "/api/storage/snapshot" if immutable else f"/api/storage/{repo}-cache",
                 "index_root": "/simple" if immutable else f"/{repo}-cache"}
        prompt = prompts.assigned(actor, peer, spec["values"][actor], route,
                                  public["users"][peer], spec["public_token"])
        history = [{"role": "user", "content": [{"text": prompt}]}]
        actor_calls = sorted(by_actor[actor], key=lambda row: row["metadata"]["turn"])
        check(f"{actor} contiguous bounded calls",
              [row["metadata"]["turn"] for row in actor_calls] == list(range(len(actor_calls)))
              and len(actor_calls) <= actions.MAX_TURNS)
        for reserved in actor_calls:
            key, turn = reserved["call_key"], reserved["metadata"]["turn"]
            if turn == actions.MAX_TURNS - 1:
                history[-1]["content"].append({"text": "This is the final turn. Return finish now."})
            request = reserved["request"]
            expected = {"modelId": model_ids[spec["model"]],
                        "system": [{"text": provider.SYSTEM}], "messages": history,
                        "inferenceConfig": {"maxTokens": provider.MAX_TOKENS}}
            check(f"{actor}/{turn} exact private history", request == expected
                  and reserved["metadata"]["run_id"] == spec["run_id"]
                  and reserved["metadata"]["study"] == "assigned")
            check(f"{actor}/{turn} durable budget join", key in ledger
                  and ledger[key]["metadata"] == reserved["metadata"]
                  and ledger[key]["request_sha256"] == hashlib.sha256(
                      json.dumps(request, ensure_ascii=False).encode()).hexdigest()
                  and ledger[key]["max_tokens"] == provider.MAX_TOKENS)
            check(f"{actor}/{turn} single terminal call", len(terminal[key]) == 1)
            if len(terminal[key]) != 1:
                continue
            ended = terminal[key][0]
            check(f"{actor}/{turn} terminal metadata", ended["metadata"] == reserved["metadata"]
                  and ended["start_utc_ns"] == reserved["start_utc_ns"]
                  and ended["end_utc_ns"] >= reserved["start_utc_ns"])
            if ended["event"] == "failed":
                check(f"{actor}/{turn} retained failure reservation",
                      ended["budget_event"]["event"] == "failed_reserved"
                      and ended["budget_event"] == ledger.get(key))
                check(f"{actor}/{turn} failure ends own loop", turn == len(actor_calls) - 1
                      and (actor, turn) not in action_by_turn
                      and sealed["outcomes"][actor]["status"] == "provider_failure")
                continue
            text = "".join(block.get("text", "") for block in
                           ended["response"].get("output", {}).get("message", {}).get("content", []))
            check(f"{actor}/{turn} committed usage", ended["budget_event"] == ledger.get(key))
            decision = action_by_turn.get((actor, turn))
            if decision is None:
                outcome = sealed["outcomes"][actor]
                check(f"{actor}/{turn} no dropped model decision", turn == len(actor_calls) - 1
                      and outcome["status"] == "time_limit" and outcome["last_call_key"] == key)
                continue
            check(f"{actor}/{turn} visible response", decision["call_key"] == key
                  and decision["returned_text"] == text)
            history.append({"role": "assistant", "content": [{"text": text or "{}"}]})
            if decision.get("invalid_action"):
                try:
                    actions.parse(text, route)
                except (ValueError, TypeError):
                    check(f"{actor}/{turn} invalid action not dispatched", not decision["dispatched"])
                else:
                    check(f"{actor}/{turn} invalid classification agrees", False)
                history.append({"role": "user", "content": [
                    {"text": '{"error":"Invalid bounded action"}'}]})
                continue
            action = actions.parse(text, route)
            check(f"{actor}/{turn} parsed model action", action == decision["action"])
            if action["action"] == "finish":
                check(f"{actor}/{turn} finish ends own loop", turn == len(actor_calls) - 1
                      and not decision["dispatched"] and sealed["outcomes"][actor]["status"] == "finished")
                continue
            if turn == actions.MAX_TURNS - 1:
                check(f"{actor}/{turn} final slot not dispatched", not decision["dispatched"]
                      and sealed["outcomes"][actor]["status"] == "final_turn_did_not_finish")
                continue
            if action["action"] == "wait":
                observation = {"waited_s": action["seconds"]}
                check(f"{actor}/{turn} wait has no network action", not decision["dispatched"])
            else:
                identifier = decision["rpc_id"]
                check(f"{actor}/{turn} unique RPC join", identifier in rpc and identifier not in rpc_ids)
                rpc_ids.add(identifier)
                if identifier not in rpc:
                    continue
                returned = rpc[identifier]
                check(f"{actor}/{turn} same confined request", returned["actor"] == actor
                      and returned["request"]["requests"] == action["requests"]
                      and returned["records"] == decision["records"] and decision["dispatched"])
                observation = {"responses": actions.visible(returned["records"])}
            check(f"{actor}/{turn} only permitted observation", decision["visible_observation"] == observation)
            history.append({"role": "user", "content": [{"text": json.dumps(observation)}]})
    check("complete RPC and call coverage", rpc_ids == set(rpc)
          and set(terminal) == set(reservations)
          and set(action_by_turn).issubset({(row["metadata"]["actor"], row["metadata"]["turn"])
                                           for row in reservations.values()}))
    return {"passed": not failures, "checks": checks, "failure_count": len(failures),
            "failures": failures[:30], "run_id": spec["run_id"], "calls": len(reservations),
            "remote_checks": checked_remote["checks"],
            "scope": "Exact model-visible histories, reservations, and confined actions; no inferred channel."}
