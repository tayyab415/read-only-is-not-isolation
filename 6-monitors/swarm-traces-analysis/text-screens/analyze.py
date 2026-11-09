#!/usr/bin/env python3
"""Read-only local Swarm Traces audit. Never run source text or use the network."""
import argparse
import collections
import csv
import gzip
import hashlib
import json
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE_URL = "https://swarmtraces.org/data/final/redacted.jsonl.gz"
SOURCE = ROOT / "library/sources/S-f210724f4511/dataset.jsonl.gz"
DB = ROOT / "library/swarm-data.sqlite"
PATTERNS = {
    "poll_word": r"\bpoll(?:ing|ed|s)?\b",
    "sleep_call": r"\b(?:sleep|usleep|nanosleep|wait_for_timeout)\s*\(",
    "wait_word": r"\bwait(?:ing|ed|s)?\b",
    "timer_call": r"\b(?:setTimeout|setInterval)\s*\(",
    "board_word": r"\b(?:message\s*boards?|boards?)\b",
    "read_syntax": r"\b(?:PROPFIND|GET)\b|\.(?:read|text)\s*\(",
    "write_syntax": r"\b(?:MKCOL|PUT|POST|PATCH)\b|\.(?:write|send)\s*\(",
    "reply_word": r"\brepl(?:y|ies|ied)\b",
    "directed_terms": r"\b(?:recipient|reply_to|in_reply_to|send_message|direct\s+message|to_agent|target_agent)\b",
    "artifactory_word": r"\bartifactory\b",
    "propfind_word": r"\bPROPFIND\b",
    "mkcol_word": r"\bMKCOL\b",
    "identity_terms": r"\b(?:agent_id|x-agent(?:-id|-name)?|author|sender)\b",
    "runtime_clock_syntax": r"\b(?:Date\.now|performance\.now|time\.time)\s*\(",
}
# Release placeholder families visible in the local rows. Code arrays remain intact.
PLACEHOLDER = re.compile(
    r"\[(?:REDACTED(?:[ :][^\]\n]*)?|ENCODED BLOB(?: \d+)?|"
    r"CREDENTIAL(?: \d+)?|SERVICE(?: [^\]\n]*)?|PROXY(?: [^\]\n]*)?|"
    r"INFRA CONFIG(?: \d+)?|HF REPO(?: \d+)?|ENV VALUE(?: \d+)?|"
    r"INTERNAL HOST(?: \d+)?|WORKLOAD ID(?: \d+)?|SHORTENER[^\]\n]*|"
    r"REDIRECT URL(?: \d+)?)\]"
)
CURATED = [
    "R0032366", "R0079625", "R0117987", "R0033477",
    "R0006010", "R0006020", "R0044963", "R0035736",
    "R0035596", "R0004960", "R0189549", "R0189550",
    "R0189571", "R0059412", "R0005989", "R0064429",
    "R0029223", "R0031717", "R0071952", "R0070267",
    "R0155625", "R0185155", "R0098308",
    "R0097792", "R0027685", "R0045289",
]


def dump(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-source-check", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    db_stat_before = [DB.stat().st_size, DB.stat().st_mtime_ns]
    schema = [dict(zip(("name", "type", "sql"), r)) for r in conn.execute(
        "SELECT name,type,sql FROM sqlite_master ORDER BY name"
    )]
    rows = {}
    keys = collections.Counter()
    value_types = collections.defaultdict(collections.Counter)
    kinds = collections.Counter()
    counts = collections.defaultdict(collections.Counter)
    payload_hashes = set()
    root_candidates = collections.defaultdict(set)
    unique_texts = collections.defaultdict(set)
    text_hashes = collections.defaultdict(list)
    candidate_records = []
    all_hashes = {}
    placeholders = collections.Counter()
    by_kind_placeholders = collections.Counter()
    tag_values = collections.Counter()
    nonempty = collections.Counter()
    min_id = None
    max_id = None
    selected = {}
    regexes = {k: re.compile(v, re.I) for k, v in PATTERNS.items()}
    for (raw,) in conn.execute("SELECT raw_json FROM rows ORDER BY row_id"):
        r = json.loads(raw)
        rid, kind, text = r["id"], r["kind"], r["text"]
        min_id = min_id or rid
        max_id = rid
        rows[rid] = (kind, r["parent_id"], r["cite"])
        keys.update(r.keys())
        for key, value in r.items():
            value_types[key][type(value).__name__] += 1
            if value is not None and value != "":
                nonempty[key] += 1
        kinds[kind] += 1
        if r["tags"]:
            tag_values[r["tags"]] += 1
        ph = PLACEHOLDER.findall(text)
        if ph:
            by_kind_placeholders[kind] += 1
            placeholders.update(ph)
        masked = PLACEHOLDER.sub(" ", text)
        raw_hit = [key for key, rx in regexes.items() if rx.search(text)]
        hit = [key for key, rx in regexes.items() if rx.search(masked)]
        for label, hits in [("raw", raw_hit), ("masked", hit)]:
            for key in hits:
                counts[f"{label}:{key}"][kind] += 1
        h = hashlib.sha256(text.encode()).hexdigest()
        if kind == "payload":
            payload_hashes.add(h)
        text_hashes[h].append(rid)
        all_hashes[rid] = hashlib.sha256(
            json.dumps(r, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        combos = {
            "board_and_read": {"board_word", "read_syntax"},
            "board_and_write": {"board_word", "write_syntax"},
            "poll_and_read": {"poll_word", "read_syntax"},
            "poll_and_sleep_or_timer": {"poll_word"},
            "artifactory_and_propfind": {"artifactory_word", "propfind_word"},
            "artifactory_and_mkcol": {"artifactory_word", "mkcol_word"},
            "artifactory_propfind_mkcol_sleep": {
                "artifactory_word", "propfind_word", "mkcol_word", "sleep_call"
            },
        }
        for name, required in combos.items():
            qualifies = required.issubset(hit)
            if name == "poll_and_sleep_or_timer":
                qualifies = qualifies and bool({"sleep_call", "timer_call"}.intersection(hit))
            if qualifies:
                hit.append(name)
                counts[f"masked:{name}"][kind] += 1
        if raw_hit or hit:
            candidate_records.append((rid, kind, r["cite"], raw_hit, hit, h))
        if rid in CURATED:
            selected[rid] = r

    def root_of(rid):
        seen = set()
        while rid in rows and rows[rid][1] is not None:
            if rid in seen:
                return "CYCLE"
            seen.add(rid)
            rid = rows[rid][1]
        return rid

    links = collections.Counter()
    orphan = []
    forward = []
    roots = collections.Counter()
    families = collections.Counter()
    depths = collections.Counter()
    for rid, (kind, parent, _) in rows.items():
        if parent:
            links[f"{kind}->{rows[parent][0] if parent in rows else 'MISSING'}"] += 1
            if parent not in rows:
                orphan.append(rid)
            if parent >= rid:
                forward.append(rid)
        else:
            roots[kind] += 1
        families[root_of(rid)] += 1
        depth, current = 0, rid
        while current in rows and rows[current][1] is not None:
            depth += 1
            current = rows[current][1]
            if depth > len(rows):
                break
        depths[depth] += 1
    for rid, kind, cite, raw_hits, masked_hits, h in candidate_records:
        for hit in masked_hits:
            root_candidates[hit].add(root_of(rid))
            unique_texts[hit].add(h)
    with (HERE / "candidates.tsv").open("w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["id", "cite", "kind", "evidence_root", "raw_rules", "masked_rules"])
        for rid, kind, cite, raw_hits, hit, _ in candidate_records:
            writer.writerow([rid, cite, kind, root_of(rid), ",".join(raw_hits), ",".join(hit)])

    source_check = {"performed": False}
    if not args.skip_source_check:
        verified = 0
        mismatch = []
        source_line = {}
        nonnumeric_line_ids = []
        with gzip.open(SOURCE, "rt") as f:
            for line_number, line in enumerate(f, 1):
                r = json.loads(line)
                if r["id"] != f"R{line_number:07d}":
                    nonnumeric_line_ids.append({"line": line_number, "id": r["id"]})
                h = hashlib.sha256(json.dumps(r, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                if all_hashes.get(r["id"]) != h:
                    mismatch.append({"line": line_number, "id": r["id"]})
                else:
                    verified += 1
                if r["id"] in selected:
                    source_line[r["id"]] = line_number
        source_check = {
            "performed": True, "matching_records": verified, "mismatches": mismatch,
            "compressed_bytes": SOURCE.stat().st_size,
            "compressed_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "selected_source_lines": source_line,
            "row_ids_not_matching_line_number": nonnumeric_line_ids,
        }
    # Exact local text equality, retaining every ID. This is a sensitivity check,
    # not an estimate of unique executions or independently issued requests.
    duplicate_groups = [v for v in text_hashes.values() if len(v) > 1]
    metrics = {
        "source_url": SOURCE_URL,
        "database": str(DB.relative_to(ROOT)),
        "schema": schema, "rows": len(rows), "kinds": kinds,
        "first_id": min_id, "last_id": max_id,
        "json_keys": keys, "value_types": value_types, "nonempty_values": nonempty,
        "parent_links": links, "orphan_rows": orphan, "forward_parent_rows": forward,
        "roots_by_kind": roots, "root_count": len(families),
        "parent_depth_counts": depths,
        "fts_rows": conn.execute("SELECT COUNT(*) FROM payloads").fetchone()[0],
        "max_evidence_family_size": max(families.values()),
        "families_by_size": collections.Counter(families.values()),
        "nonempty_tag_rows": sum(tag_values.values()),
        "tag_values": tag_values,
        "unique_exact_texts": len(text_hashes),
        "payload_unique_exact_texts": len(payload_hashes),
        "duplicate_text_groups": len(duplicate_groups),
        "rows_in_duplicate_text_groups": sum(map(len, duplicate_groups)),
        "placeholder_rows_by_kind": by_kind_placeholders,
        "placeholder_occurrences": sum(placeholders.values()),
        "patterns": PATTERNS,
        "placeholder_pattern": PLACEHOLDER.pattern,
        "counts": counts,
        "masked_rule_unique_evidence_roots": {k: len(v) for k, v in root_candidates.items()},
        "masked_rule_unique_exact_texts": {k: len(v) for k, v in unique_texts.items()},
        "source_check": source_check,
        "database_size_mtime_unchanged": db_stat_before == [DB.stat().st_size, DB.stat().st_mtime_ns],
    }
    dump("metrics.json", metrics)
    case_dir = HERE / "rows"
    case_dir.mkdir(exist_ok=True)
    for rid, r in selected.items():
        (case_dir / f"{rid}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n")
        (case_dir / f"{rid}.txt").write_text(r["text"])
    # Deterministic inspection queue, spread across the ordered corpus.
    sample = []
    for rule in ["poll_word", "sleep_call", "wait_word", "board_word", "reply_word",
                 "directed_terms", "artifactory_and_propfind", "artifactory_and_mkcol"]:
        candidates = [r for r in candidate_records if rule in r[4]]
        for index in sorted({round(i * (len(candidates) - 1) / 7) for i in range(8)}):
            if candidates:
                r = candidates[index]
                sample.append({"rule": rule, "id": r[0], "cite": r[2], "kind": r[1]})
    dump("inspection-queue.json", sample)
    dump("duplicate-examples.json", sorted(duplicate_groups, key=lambda x: (-len(x), x[0]))[:20])
    print(json.dumps({
        "rows": metrics["rows"], "kinds": kinds, "nonempty": nonempty,
        "parent_links": links, "roots": roots,
        "source_check": source_check,
        "counts": {k: dict(v) for k, v in counts.items() if k.startswith("masked:")},
    }, indent=2))


if __name__ == "__main__":
    main()
