#!/usr/bin/env python3
"""Audit publisher hashes, frozen inputs, pip reports, and retained outcomes."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((DATA / "manifest.json").read_text())
    freeze = json.loads((DATA / "utility-frozen.json").read_text())
    results = json.loads((DATA / "utility-results.json").read_text())
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    check("manifest hash", sha(DATA / "manifest.json") == freeze["manifest_sha256"])
    for name, expected in freeze["sha256"].items():
        check(f"frozen source {name}", sha(HERE / name) == expected)
    for name, key in [("requirements.txt", "requirements_sha256"),
                      ("constraints.txt", "constraints_sha256"), ("PROTOCOL.md", "protocol_sha256"),
                      ("admit.py", "admission_source_sha256")]:
        check(f"admission input {name}", sha(HERE / name) == manifest[key])
    entries = {entry["canonical"]: entry for entry in manifest["entries"]}
    routes = {f"/files/{entry['sha256']}/{entry['filename']}": entry for entry in entries.values()}
    for name, entry in entries.items():
        check(f"content {name}", sha(DATA / entry["path"]) == entry["sha256"])
        raw = DATA / "publisher" / f"{name}-{entry['version']}.json"
        check(f"publisher record {name}", sha(raw) == entry["publisher_json_sha256"])
        files = [item for item in json.loads(raw.read_text())["urls"] if item["filename"] == entry["filename"]]
        check(f"publisher agreement {name}", len(files) == 1
              and files[0]["digests"]["sha256"] == entry["sha256"]
              and files[0]["url"] == entry["publisher_file_url"])
    counts = Counter((row["tenant"], row["requirement"]) for row in results["tasks"])
    requirements = (HERE / "requirements.txt").read_text().splitlines()
    check("complete 28-task roster", len(results["tasks"]) == 28
          and counts == Counter((tenant, requirement) for tenant in ["alice", "bob"] for requirement in requirements))
    service_summary = {}
    for tenant in ["alice", "bob"]:
        rows = [json.loads(line) for line in (DATA / f"service-{tenant}.jsonl").read_text().splitlines()]
        port = results["service_readiness"][tenant]["port"]
        for task in [row for row in results["tasks"] if row["tenant"] == tenant]:
            folder = DATA / "utility" / task["task_id"]
            report = json.loads((folder / "pip-report.json").read_text())
            check(f"{task['task_id']} observed success", task["pip_exit_code"] == 0
                  and task["function_exit_code"] == 0 and task["passed"]
                  and json.loads((folder / "function.txt").read_text())["function_passed"])
            for item in report["install"]:
                url = urlsplit(item["download_info"]["url"])
                expected = routes.get(url.path)
                check(f"{task['task_id']} admitted dependency {item['metadata']['name']}",
                      expected is not None and url.hostname == "127.0.0.1" and url.port == port
                      and item["metadata"]["version"] == expected["version"]
                      and item["download_info"]["archive_info"]["hashes"]["sha256"] == expected["sha256"])
        observed = Counter(row["path"] for row in rows if row["path"] in routes)
        expected = Counter()
        for task in [row for row in results["tasks"] if row["tenant"] == tenant]:
            report = json.loads((DATA / "utility" / task["task_id"] / "pip-report.json").read_text())
            expected.update(urlsplit(item["download_info"]["url"]).path for item in report["install"])
        check(f"{tenant} wheel request count matches pip reports", observed == expected)
        check(f"{tenant} successful service responses", all(row["status"] == 200 for row in rows))
        check(f"{tenant} assigned service log", all(row["tenant"] == tenant for row in rows))
        check(f"{tenant} preloaded serving records no upstream", all(row["upstream_requests"] == 0 for row in rows))
        cleanup = results["cleanup"][tenant]
        check(f"{tenant} cleanup", cleanup["exit_code"] is not None and cleanup["socket_closed"])
        service_summary[tenant] = {"requests": len(rows), "wheel_downloads": sum(observed.values()),
                                   "delivered_bytes": sum(row["body_bytes"] for row in rows)}
    check("manifest and content unchanged", results["content_unchanged"] and results["manifest_unchanged"])
    output = {"checks": len(checks), "passed": all(row["passed"] for row in checks),
              "failures": [row for row in checks if not row["passed"]],
              "service_summary": service_summary, "manifest_sha256": sha(DATA / "manifest.json"),
              "utility_results_sha256": sha(DATA / "utility-results.json"),
              "audit_source_sha256": sha(Path(__file__)),
              "scope": "Audit of retained local utility evidence, not independent network or sandbox attestation."}
    (DATA / "utility-audit.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
