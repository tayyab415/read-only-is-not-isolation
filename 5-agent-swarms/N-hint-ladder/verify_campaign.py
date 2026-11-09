#!/usr/bin/env python3
"""Audit only the frozen campaign plans, keeping legacy failures separate."""
import hashlib
import argparse
import json
from pathlib import Path
import re
import sys
from collections import Counter
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "O-monitors"))
from score import registry
from verify import PAYLOAD_SHA, extract_action, read_jsonl, verify_cohort


def read_json(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stages", nargs="+")
    args = parser.parse_args()
    results = []
    total_actions, http_count, artifact_count = 0, 0, 0
    utility = Counter()
    convenience_metadata = []
    for spec in registry():
        if args.stages and spec["stage"] not in args.stages:
            continue
        folder = ROOT / "data" / spec["cohort_id"]
        pre, summary = read_json(folder / "preregister.json"), read_json(folder / "summary.json")
        keys = ["cohort_id", "model", "kind", "hint", "condition", "alice_value", "bob_value", "seed"]
        if "variants" in spec:
            keys.append("variants")
        match = all(pre.get(key) == spec.get(key) for key in keys)
        if not match:
            raise RuntimeError(f"frozen plan mismatch {folder.name}")
        if summary["outcome"] == "infra_error":
            results.append({"cohort": folder.name, "excluded": "infra_error", "plan_match": match})
            continue
        old = read_json(folder / "verification-v2.json")
        if old["failed"]:
            backup = folder / "verification-v2.before-final-audit.json"
            if not backup.exists():
                backup.write_text(json.dumps(old, indent=2) + "\n")
        audit = verify_cohort(folder)
        if audit["failed"]:
            raise RuntimeError(f"strict evidence failure {folder.name}: {audit['failed']}")
        calls = read_jsonl(folder / "calls.jsonl")
        completed = {(r["agent"], r["action_index"]): r for r in calls if r["event"] == "completed"}
        reserved = {(r["agent"], r["action_index"]): r for r in calls if r["event"] == "reserved"}
        assert len(completed) == sum(r["event"] == "completed" for r in calls)
        assert len(reserved) == sum(r["event"] == "reserved" for r in calls)
        action_rows = read_jsonl(folder / "actions.jsonl")
        auth_count, history_count = 0, 0
        previous = {}
        for call in sorted(reserved.values(), key=lambda r: (r["agent"], r["action_index"])):
            agent, index = call["agent"], call["action_index"]
            messages = call["request"]["messages"]
            assert call["request"]["modelId"] == pre["model_id"], (folder.name, agent, index, "model ID")
            assert len(messages) == 2 * index + 1, (folder.name, agent, index, "history length")
            assert messages[0]["content"][0]["text"] == pre["prompts"][agent]
            if agent in previous:
                old_messages = previous[agent]
                assert messages[:len(old_messages)] == old_messages, (folder.name, agent, index, "history prefix")
            previous[agent] = messages
            history_count += 1
        artifact_by_agent = Counter()
        for row in action_rows:
            key = row["agent"], row["action_index"]
            reply, request = completed[key], reserved[key]
            assert reply["call_key"] == request["call_key"], (folder.name, key, "call identity")
            raw_text = "".join(c.get("text", "") for c in
                               reply["response"]["output"]["message"]["content"])
            if "raw_text" in row["action"]:
                assert raw_text == row["action"]["raw_text"], (folder.name, key, "invalid output")
            else:
                assert extract_action(reply["response"]) == row["action"], (folder.name, key, "output action")
            auth_count += 1
            if row.get("marker"):
                http_count += 1
            if (row.get("method") == "GET" and row.get("status") == 200 and
                re.fullmatch(rf"/{re.escape(pre['repo'])}(?:-cache)?/pkg-(?:0[0-9]|1[0-5])\.bin(?:\?.*)?",
                             row.get("path", ""))):
                if "properties" in parse_qs(urlsplit(row["path"]).query, keep_blank_values=True):
                    convenience_metadata.append({"cohort": folder.name, "marker": row["marker"],
                                                 "path": row["path"]})
                    continue
                body = row["sandbox"]["body"]
                assert hashlib.sha256(body.encode()).hexdigest() == PAYLOAD_SHA, (folder.name, key, "fixture bytes")
                artifact_count += 1
                artifact_by_agent[row["agent"]] += 1
        for agent in ["alice", "bob"]:
            if artifact_by_agent[agent]:
                utility[(spec["stage"], spec["condition"], spec["model"])] += 1
        total_actions += len(action_rows)
        results.append({
            "cohort": folder.name, "stage": spec["stage"], "plan_match": match,
            "strict_checks": audit["checks"], "authenticated_action_outputs": auth_count,
            "full_history_requests": history_count,
            "fixture_gets_by_agent": dict(artifact_by_agent),
        })
    scripted = []
    for cid in ["n0fix-o00", "n0fix-o01", "n0fix-c00", "n0fix-c01"]:
        report = verify_cohort(ROOT / "data" / cid)
        assert not report["failed"], cid
        scripted.append({"cohort": cid, "checks": report["checks"], "causal_results": report["causal_results"]})
    report = {
        "registered_cohorts": len(results),
        "excluded_infra": [r["cohort"] for r in results if r.get("excluded")],
        "strict_checks": sum(r.get("strict_checks", 0) for r in results),
        "authenticated_action_outputs": total_actions, "joined_http_actions": http_count,
        "fixture_gets_verified": artifact_count, "fixture_sha256": PAYLOAD_SHA,
        "convenience_property_views": convenience_metadata,
        "full_history_requests": sum(r.get("full_history_requests", 0) for r in results),
        "model_agents_with_fixture_get": [
            {"stage": k[0], "condition": k[1], "model": k[2], "agents": v}
            for k, v in sorted(utility.items())
        ],
        "scripted": scripted, "cohorts": results,
        "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [Path(__file__), ROOT / "verify.py", ROOT / "run.py"]},
        "scope": "frozen A/B/BN/BNR/C1 plans plus explicit A2/B2 repair mappings; legacy evidence not pooled",
    }
    name = ("campaign-verification." + "-".join(args.stages) + ".json"
            if args.stages else "campaign-verification.json")
    (ROOT / "data" / name).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ["registered_cohorts", "excluded_infra", "strict_checks",
                                            "authenticated_action_outputs", "joined_http_actions",
                                            "fixture_gets_verified", "full_history_requests"]}))


if __name__ == "__main__":
    main()
