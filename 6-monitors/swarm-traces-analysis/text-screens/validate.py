#!/usr/bin/env python3
"""Check reported source citations and saved evidence against the read-only DB."""
import json
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPORT = ROOT / "artifactory-lab/reports/E-swarm-traces-sol.md"
CONN = sqlite3.connect((ROOT / "library/swarm-data.sqlite").as_uri() + "?mode=ro", uri=True)
CONN.execute("PRAGMA query_only=ON")
problems = []
checked = {}
for rid, suffix, url_rid in re.findall(
    r"\[(R\d{7}):([0-9a-f]{8})\]\(https://swarmtraces\.org/viewer/#/row/(R\d{7})\)",
    REPORT.read_text(),
):
    row = CONN.execute("SELECT cite FROM rows WHERE row_id=?", (rid,)).fetchone()
    if rid != url_rid or row is None or row[0] != rid + ":" + suffix:
        problems.append(["citation_mismatch", rid, suffix, url_rid])
    checked[rid] = row[0] if row else None

saved_rows = 0
for path in sorted((HERE / "rows").glob("*.json")):
    record = json.loads(path.read_text())
    original = json.loads(CONN.execute("SELECT raw_json FROM rows WHERE row_id=?", (record["id"],)).fetchone()[0])
    if record != original or path.with_suffix(".txt").read_text() != original["text"]:
        problems.append(["saved_row_mismatch", record["id"]])
    saved_rows += 1

excerpts = 0
for name in ["manual-review.json", "reply-review.json", "timestamp-example.json"]:
    values = json.loads((HERE / name).read_text())
    if isinstance(values, dict):
        values = [values]
    for value in values:
        record = json.loads(CONN.execute("SELECT raw_json FROM rows WHERE row_id=?", (value["id"],)).fetchone()[0])
        if record["cite"] != value["cite"]:
            problems.append(["evidence_cite_mismatch", value["id"]])
        for excerpt in value["evidence"]:
            if record["text"][excerpt["start_char"]:excerpt["end_char"]] != excerpt["exact_text"]:
                problems.append(["excerpt_mismatch", value["id"]])
            excerpts += 1

broken_local_links = []
for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", REPORT.read_text()):
    if "://" not in target and not (REPORT.parent / target).exists():
        broken_local_links.append(target)
problems.extend(["missing_local_link", target] for target in broken_local_links)

metrics = json.loads((HERE / "metrics.json").read_text())
review = json.loads((HERE / "review-summary.json").read_text())
assert metrics["rows"] == 189579
assert metrics["source_check"]["matching_records"] == 189579
assert not metrics["source_check"]["mismatches"]
assert not metrics["source_check"]["row_ids_not_matching_line_number"]
assert metrics["database_size_mtime_unchanged"]
assert metrics["nonempty_values"].get("time_utc", 0) == 0
assert metrics["parent_depth_counts"] == {"0": 128454, "1": 61125}
assert metrics["fts_rows"] == 189579
assert review["all_reply_candidates_reviewed"] == 21
assert review["reply_labels"] == {"local_io_reply": 13, "nfs_protocol_reply": 4, "chatbot_test_prompt": 4}
result = {
    "report_source_citations_checked": len(checked),
    "saved_original_rows_checked": saved_rows,
    "exact_excerpts_checked": excerpts,
    "source_records_compared": metrics["source_check"]["matching_records"],
    "problems": problems,
}
(HERE / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
assert not problems, problems
