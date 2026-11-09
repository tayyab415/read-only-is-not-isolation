#!/usr/bin/env python3
"""Report all scheduled subject pairs, including provider and budget censoring."""
import argparse
import json
from pathlib import Path

import call_audit
import gate
import known_statistics
import schedule


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-data", type=Path, required=True)
    args = parser.parse_args()
    gate.sources()
    rows = []
    for spec in schedule.assigned():
        folder = gate.HERE / "data" / spec["run_id"]
        remote = args.remote_data / spec["run_id"]
        if not (folder / "sealed.json").exists() or not (remote / "sealed.json").exists():
            rows.append({"run_id": spec["run_id"], "model": spec["model"],
                         "configuration": spec["configuration"], "collected": False,
                         "scheduled_agents": 2, "reason": "No complete paired local/remote seal."})
            continue
        audit = call_audit.check_cohort(folder, remote)
        (folder / "call-audit-results.json").write_text(json.dumps(audit, indent=2))
        sealed = json.loads((folder / "sealed.json").read_text())
        public = json.loads((folder / "provision.json").read_text())
        decisions = call_audit.audit.rows(folder / "client-actions.jsonl") \
            if (folder / "client-actions.jsonl").exists() else []
        if not audit["passed"]:
            rows.append({"run_id": spec["run_id"], "model": spec["model"],
                         "configuration": spec["configuration"], "collected": True,
                         "audit_passed": False, "scheduled_agents": 2,
                         "outcomes": sealed.get("outcomes"), "audit_failures": audit["failures"]})
            continue
        witnesses = {actor: known_statistics.assess(
            spec, public, decisions, sealed["outcomes"], actor) for actor in ("alice", "bob")}
        rows.append({"run_id": spec["run_id"], "model": spec["model"],
                     "configuration": spec["configuration"], "collected": True,
                     "audit_passed": True, "scheduled_agents": 2,
                     "outcomes": sealed["outcomes"], "known_mechanism_assessment": witnesses,
                     "calls": audit["calls"], "http_requests": sealed["remote_close"]["status"]["http_requests"]})
    output = {"scheduled_pairs": len(rows), "scheduled_agents": 2 * len(rows), "rows": rows,
              "withdrawals": [schedule.WITHDRAWAL],
              "source_freeze_sha256": gate.sha(gate.HERE / "SOURCES-FROZEN.json"),
              "scope": "Exploratory assigned clients. Other proposed mechanisms require fresh scripted confirmation.",
              "ordinary_task_results_included": False}
    (gate.HERE / "assigned-results.json").write_text(json.dumps(output, indent=2))
    print(json.dumps({"scheduled_pairs": len(rows), "collected_pairs": sum(row["collected"] for row in rows),
                      "audited_pairs": sum(row.get("audit_passed", False) for row in rows)}, indent=2))


if __name__ == "__main__":
    main()
