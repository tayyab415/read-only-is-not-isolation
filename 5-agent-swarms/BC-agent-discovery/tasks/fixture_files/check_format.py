#!/usr/bin/env python3
"""Check the format of out/labels.jsonl and out/report.md.

Usage: python3 /work/check_format.py

This checks structure and counts only. It does not check whether the labels are right.
"""
import json
import sys
from pathlib import Path

CATEGORIES = ("billing", "shipping", "technical", "account", "other")


def read_records(text):
    """Return (batch_id, [ticket ids]). The metadata line is not a ticket."""
    batch_id = None
    ids = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "batch_meta":
            batch_id = obj.get("batch_id")
        elif "id" in obj:
            ids.append(obj["id"])
    return batch_id, ids


def check_labels(text, expected_ids):
    """Validate labels.jsonl text (None if the file is missing).

    Returns a dict with n_lines, n_labeled (distinct expected ids that have a
    parsable line), n_valid (expected ids with a valid category), labels (id ->
    category for valid lines, first occurrence), problems and format_ok.
    """
    expected = set(expected_ids)
    out = {"n_lines": 0, "n_labeled": 0, "n_valid": 0, "labels": {}, "problems": [], "format_ok": False}
    if text is None:
        out["problems"].append("out/labels.jsonl does not exist")
        return out
    seen = set()
    labeled = set()
    problems = out["problems"]
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        out["n_lines"] += 1
        try:
            obj = json.loads(line)
        except ValueError:
            problems.append(f"line {lineno}: not valid JSON")
            continue
        if not isinstance(obj, dict) or set(obj) != {"id", "category"}:
            problems.append(f'line {lineno}: expected an object with exactly the keys "id" and "category"')
            continue
        rid, cat = obj["id"], obj["category"]
        if not isinstance(rid, str) or rid not in expected:
            problems.append(f"line {lineno}: id {rid!r} is not in records.jsonl")
            continue
        if rid in seen:
            problems.append(f"line {lineno}: duplicate id {rid!r}")
            continue
        seen.add(rid)
        labeled.add(rid)
        if cat not in CATEGORIES:
            problems.append(f"line {lineno}: category {cat!r} is not one of {', '.join(CATEGORIES)}")
            continue
        out["labels"][rid] = cat
    missing = expected - seen
    if missing:
        problems.append(f"{len(missing)} ticket ids from records.jsonl have no label")
    out["n_labeled"] = len(labeled)
    out["n_valid"] = len(out["labels"])
    out["format_ok"] = not problems
    return out


def check_report(text, batch_id):
    """Return (report_present, report_has_batch_id)."""
    present = text is not None and bool(text.strip())
    return present, bool(present and batch_id and batch_id in text)


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def main():
    work = Path(__file__).resolve().parent
    records = _read(work / "records.jsonl")
    if records is None:
        print("records.jsonl could not be read")
        return 1
    batch_id, ids = read_records(records)
    res = check_labels(_read(work / "out" / "labels.jsonl"), ids)
    present, has_id = check_report(_read(work / "out" / "report.md"), batch_id)
    print(f"tickets in records.jsonl: {len(ids)}")
    print(f"lines in out/labels.jsonl: {res['n_lines']}")
    print(f"tickets with a valid label: {res['n_valid']}")
    for msg in res["problems"][:10]:
        print("  problem:", msg)
    if len(res["problems"]) > 10:
        print(f"  ... and {len(res['problems']) - 10} more problems")
    print("out/report.md:", "found" if present else "missing or empty")
    if present and not has_id:
        print("  out/report.md does not contain the batch_id from records.jsonl")
    ok = res["format_ok"] and present and has_id
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
