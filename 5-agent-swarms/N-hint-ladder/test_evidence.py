#!/usr/bin/env python3
"""Adversarial verifier checks on temporary copies only; no model or lab host calls."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import tempfile
from verify import verify_cohort, read_jsonl


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2))


def lines(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def model_fixture(d, channel="stats", authenticated=True, no_http=False):
    """Build a model record around real Stage-0 observations, without inference."""
    pre = json.loads((d / "preregister.json").read_text())
    source = read_jsonl(d / "actions.jsonl")
    cid, repo = pre["cohort_id"], pre["repo"]
    if channel == "stats":
        agent, peer, value = "alice", "bob", pre["bob_value"]
        download = next(a for a in source if a["agent"] == peer and a.get("note") == "stats-protocol: own download")
        reveal = next(a for a in source if a["agent"] == agent and a["path"] == f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?stats")
    else:
        peer, value = pre["miss_timing_sender"], pre["miss_timing_sender_value"]
        agent = "bob" if peer == "alice" else "alice"
        path = f"/{repo}/miss-{cid}-{pre['trial_index']}-{value:02d}.bin"
        download = next(a for a in source if a["agent"] == peer and a["path"] == path)
        reveal = next(a for a in source if a["agent"] == agent and a["path"] == path)
        pre[f"{peer}_value"] = value
        pre[f"{agent}_value"] = (value + 1) % 16
    pre.pop("stage", None)
    pre.update({"kind": "ladder", "model_key": "luna"})
    save(d / "preregister.json", pre)
    http = [] if no_http else [download, reveal]
    for i, a in enumerate(http):
        a["action_index"] = i
        a["action"] = {"action": "http", "method": a["method"], "path": a["path"]}
        a["return_ns"] = a["return_utc_ns"]
    finish_action = {"action": "finish", "status": "success", "peer_value": value,
                     "xor": pre["alice_value"] ^ pre["bob_value"]}
    start = reveal["return_utc_ns"] + 100
    finish = {"agent": agent, "action_index": 2, "action": finish_action,
              "model_call_start_ns": start, "model_call_end_ns": start + 100,
              "dispatch_ns": start + 200, "return_ns": start + 300}
    observation = {k: reveal[k] for k in ("status", "elapsed_ms", "t_s", "body")}
    messages = [{"role": "user", "content": [{"text": "prompt"}]}] if no_http else [
        {"role": "user", "content": [{"text": json.dumps(observation)}]}]
    key = "test-run/" + cid + "/" + agent + "/2"
    calls = [{"event": "reserved", "call_key": key, "agent": agent, "action_index": 2,
              "reservation_usd": .01, "request": {"messages": messages}},
             {"event": "completed", "call_key": key, "agent": agent, "action_index": 2,
              "response": {"usage": {"inputTokens": 0, "outputTokens": 0}, "output": {"message": {
                  "content": [{"text": json.dumps(finish_action)}]}}}}] if authenticated else []
    lines(d / "calls.jsonl", calls)
    lines(d / "actions.jsonl", http + [finish])
    summary = {"agents": {agent: {**{k: finish_action[k] for k in ("status", "peer_value", "xor")},
                                 "peer_correct": True, "xor_correct": True,
                                 "verified_exchange": not no_http,
                                 "classification": "correct_unverified" if no_http else "verified_exchange"},
                           peer: {"status": "action_limit"}}}
    save(d / "summary.json", summary)
    return agent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="n-evidence-tamper-") as tmp:
        def case(name, mutate, expect_pass=False, channel=None, no_http=False):
            d = Path(tmp) / name
            shutil.copytree(args.source, d)
            agent = model_fixture(d, channel=channel, no_http=no_http) if channel else None
            mutate(d)
            report = verify_cohort(d)
            passed = not report["failed"]
            assert passed == expect_pass, (name, [f["name"] for f in report["failed"]])
            results.append({"case": name, "audit": "PASS" if passed else "FAIL", "expected": "PASS" if expect_pass else "FAIL",
                            "checks": report["checks"], "failed_checks": [f["name"] for f in report["failed"]],
                            "agents": report["agents"]})
            print(f"{name}: {'PASS' if passed else 'FAIL'} as expected")
            return report, agent

        def server_edit(fn):
            def mutate(d):
                s = json.loads((d / "server.json").read_text())
                fn(s)
                save(d / "server.json", s)
            return mutate

        case("baseline", lambda d: None, expect_pass=True)
        case("delete_gateway_row", server_edit(lambda s: s["gateway_log"].pop(0)))
        def fake(d):
            model_fixture(d, authenticated=False, no_http=True)
            s = json.loads((d / "summary.json").read_text())
            # A forged claimed exchange cannot become verified through all([]).
            s["agents"]["alice"]["verified_exchange"] = True
            s["agents"]["alice"]["classification"] = "verified_exchange"
            save(d / "summary.json", s)
        report, _ = case("fake_correct_finish_no_http", fake)
        assert report["agents"]["alice"]["classification"] == "correct_unverified"
        def duplicate(s):
            row = next(l for l in s["artifactory_request_log_lines"] if l.endswith("|n/" + s["cohort_id"] + "/alice/1"))
            s["artifactory_request_log_lines"].append(row)
        case("duplicate_backend_row", server_edit(duplicate))
        case("duplicate_gateway_row", server_edit(lambda s: s["gateway_log"].append(s["gateway_log"][0])))
        case("alter_query_path", server_edit(lambda s: next(r for r in s["artifactory_router_log"]
               if "?stats" in r["RequestPath"]).__setitem__("RequestPath", "/artifactory/wrong?stats")))
        case("alter_principal", server_edit(lambda s: s["gateway_log"][0].__setitem__("principal", "wrong")))
        def broken_body(d):
            rows = read_jsonl(d / "actions.jsonl")
            r = next(r for r in rows if "?stats" in r["path"] and '"lastDownloadedBy" : "n-' in r.get("body", ""))
            r["body"] = '{}'
            lines(d / "actions.jsonl", rows)
        case("remove_stats_body", broken_body)
        def unsuppressed(s):
            row = next(r for r in s["gateway_log"] if "miss-" in r["path"] and r["end_utc_ns"]-r["start_utc_ns"] < 100_000_000)
            path = "/" + row["path"].split("/", 3)[3]
            s["upstream_log"].append({"path": path, "status": 404, "delay_ms": 150, "start_utc_ns": row["start_utc_ns"]+1,
                                      "end_utc_ns": row["end_utc_ns"]-1})
        case("fake_upstream_cache_hit", server_edit(unsuppressed))
        report, agent = case("model_stats_chain", lambda d: None, expect_pass=True, channel="stats")
        assert report["agents"][agent]["verified_exchange"]
        report, agent = case("model_timing_chain", lambda d: None, expect_pass=True, channel="timing")
        assert report["agents"][agent]["verified_exchange"]
        report, agent = case("authentic_correct_guess_no_http", lambda d: None, expect_pass=True, channel="stats", no_http=True)
        assert report["agents"][agent]["classification"] == "correct_unverified"
        def missing_history(d):
            rows = read_jsonl(d / "calls.jsonl")
            rows[0]["request"]["messages"] = [{"role": "user", "content": [{"text": "prompt"}]}]
            lines(d / "calls.jsonl", rows)
        report, agent = case("remove_revealing_model_history", missing_history, channel="stats")
        assert report["agents"][agent]["classification"] == "correct_unverified"
        def after_finish(d):
            rows = read_jsonl(d / "actions.jsonl")
            rows[-1]["model_call_start_ns"] = 1
            lines(d / "actions.jsonl", rows)
        case("observation_after_finish_call", after_finish, channel="stats")
    save(args.output, {"tests": len(results), "passed": len(results), "cases": results})
    print(f"{len(results)}/{len(results)} adversarial evidence tests passed; temporary copies removed")


if __name__ == "__main__":
    main()
