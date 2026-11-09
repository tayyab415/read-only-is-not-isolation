"""Compare original P1 logs with read-only recovery. Never change trial data."""
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
P1 = HERE.parent / "P1-meter"
RUNS = ["open", "combined", "separate", "aa", "sham"]


def ns(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1e9)


def main():
    recovered = {run: [] for run in RUNS}
    sources, complete = [], False
    with gzip.open(HERE / "native-recovery.jsonl.gz", "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row["kind"] == "row":
                run = row["raw"].split("p1/m1-", 1)[1].split("/", 1)[0]
                recovered[run].append(row)
            elif row["kind"] == "source":
                sources.append(row)
            elif row["kind"] == "complete":
                complete = row["source_count"] == len(sources)
    assert complete
    results = {}
    for run in RUNS:
        folder = P1 / "data/main-m1" / run
        original = set((folder / "server-log-lines.txt").read_text().splitlines())
        arts, routes = {}, {}
        duplicates = []
        for row in recovered[run]:
            line = row["raw"]
            if line.startswith("{"):
                value = json.loads(line)
                marker = value["request_User-Agent"]
                mapping = routes
            else:
                fields = line.split("|")
                marker = fields[10]
                value = {"trace": fields[1], "stamp": fields[0]}
                mapping = arts
            if marker in mapping:
                duplicates.append(marker)
            mapping[marker] = value
        counts = Counter()
        failures = []
        emission_examples = []
        for split in ["calibration", "holdout"]:
            with (folder / (split + ".jsonl")).open() as stream:
                for line in stream:
                    trial = json.loads(line)
                    for request in trial["pre"] + trial["post"] + trial["sender_records"]:
                        marker = request["marker"]
                        if request.get("target") == "dummy":
                            continue
                        if marker not in arts:
                            # Storage-policy denials must have no backend row.
                            if request["status"] == 403 and request["path"].startswith("/api/storage/"):
                                counts["policy_denials"] += 1
                                continue
                            failures.append([marker, "missing_art"])
                            continue
                        route, art = routes.get(marker), arts[marker]
                        if route is None:
                            failures.append([marker, "missing_router"])
                            continue
                        counts["forwarded"] += 1
                        lower = request["start_utc_ns"] - 2_000_000
                        upper = request["end_utc_ns"] + 2_000_000
                        if art["trace"] != route["TraceId"]:
                            failures.append([marker, "trace_mismatch"])
                        start = ns(route["StartUTC"])
                        if not lower <= start <= start + route["Duration"] <= upper:
                            failures.append([marker, "router_interval"])
                        stamp = ns(art["stamp"])
                        if not lower <= stamp <= upper:
                            counts["art_stamp_outside_client"] += 1
                            emission_examples.append({
                                "marker": marker, "art_stamp": art["stamp"],
                                "router_start": route["StartUTC"],
                                "router_duration_ns": route["Duration"],
                                "client_end_utc_ns": request["end_utc_ns"],
                                "stamp_after_client_end_ms":
                                    (stamp - request["end_utc_ns"]) / 1e6,
                                "trace": art["trace"],
                            })
        new_lines = {row["raw"] for row in recovered[run]}
        results[run] = {
            "counts": dict(counts), "failures": failures,
            "duplicate_markers": duplicates,
            "recovered_rows": len(recovered[run]),
            "original_rows": len(original),
            "new_rows": len(new_lines - original),
            "original_rows_absent_from_recovery": len(original - new_lines),
            "emission_examples": emission_examples,
        }
    output = {
        "recovery_sha256": hashlib.sha256(
            (HERE / "native-recovery.jsonl.gz").read_bytes()).hexdigest(),
        "sources": sources, "runs": results,
        "scope": "Exact unchanged client data; no observation, label, fit, or statistic edits.",
    }
    (HERE / "diagnosis.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({run: {k: v for k, v in row.items()
                           if k != "emission_examples"}
                      for run, row in results.items()}, indent=2))


if __name__ == "__main__":
    main()
