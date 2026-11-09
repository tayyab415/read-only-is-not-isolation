#!/usr/bin/env python3
"""Fixed-threshold timing transfer with negative-cache-off control."""
import copy
import json
import random
from probe import OUT, ROUTES, RUN, req, require

state = json.loads((OUT / "remote-state.json").read_text())
REPO = state["repo"]
ROUTES.update({"h-remote1": "h-sender", "h-remote2": "h-receiver"})
bits = [0] * 8 + [1] * 8
random.Random(2208).shuffle(bits)
THRESHOLD_NS = 80_000_000


def timed_read(label, path, who):
    status, _ = req(label, "GET", path, who)
    # Another test may be writing the ledger concurrently, so match our exact label.
    records = [json.loads(x) for x in (OUT / "requests.jsonl").read_text().splitlines()]
    record = next(r for r in reversed(records) if r["run"] == RUN and r["id"] == label)
    return status, record["duration_ns"]


def main():
    (OUT / "timing-preregister.json").write_text(json.dumps({
        "bits": bits, "seed": 2208, "threshold_ns": THRESHOLD_NS,
        "upstream_missing_head_delay_s": 0.15,
        "rule": "Decode 1 for a receiver duration below 80 ms, otherwise 0.",
        "arms": ["default-negative-cache", "negative-cache-disabled"],
        "note": "Artificial local upstream delay; thresholds do not transfer to production.",
    }, indent=2))
    stat_path = f"/api/storage/{REPO}-cache/redirect/cache.bin?stats"
    before = json.loads(require(req("remote-stat-before", "GET", stat_path, "h-remote2")))
    require(req("remote-stat-sender-read", "GET", f"/{REPO}/redirect/cache.bin", "h-remote1"))
    after = json.loads(require(req("remote-stat-after", "GET", stat_path, "h-remote2")))
    (OUT / "remote-stat-summary.json").write_text(json.dumps({"before": before, "after": after}, indent=2))
    results = []
    for arm in ("default-negative-cache", "negative-cache-disabled"):
        if arm == "negative-cache-disabled":
            changed = copy.deepcopy(state["config"])
            changed["advanced"]["cache"]["missedRetrievalCachePeriodSecs"] = 0
            require(req("disable-negative-cache", "PUT", "/ui/admin/repositories", body=changed))
            require(req("disabled-cache-config", "GET", "/ui/admin/repositories/remote/" + REPO))
        for i, bit in enumerate(bits):
            path = f"/{REPO}/missing/{RUN}-{arm}-{i}.bin"
            if bit:
                timed_read(f"timing-{arm}-{i}-prime", path, "h-remote1")
            status, duration = timed_read(f"timing-{arm}-{i}-read", path, "h-remote2")
            row = {"arm": arm, "index": i, "bit": bit, "status": status,
                   "duration_ns": duration, "decoded": int(duration < THRESHOLD_NS),
                   "path": path}
            results.append(row)
            with (OUT / "timing-trials.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
    require(req("restore-negative-cache", "PUT", "/ui/admin/repositories", body=state["config"]))
    summary = {}
    for arm in ("default-negative-cache", "negative-cache-disabled"):
        rows = [r for r in results if r["arm"] == arm]
        summary[arm] = {
            "trials": len(rows), "matches": sum(r["decoded"] == r["bit"] for r in rows),
            "decoded_ones": sum(r["decoded"] for r in rows),
            "statuses": sorted({r["status"] for r in rows}),
        }
    (OUT / "timing-summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
