#!/usr/bin/env python3
"""Eight scripted HTTP requests per configuration, with zero model calls."""
import json
from pathlib import Path

import gate
from run import Bridge

HERE = Path(__file__).resolve().parent


def main():
    gate.sources()
    output = HERE / "smoke-controller-s2"
    output.mkdir(exist_ok=False)
    results = []
    for configuration in ("open", "separate", "immutable"):
        spec = {"run_id": f"p4-s2-{configuration}", "configuration": configuration}
        bridge = Bridge(spec, smoke=True)
        try:
            records = []
            for actor in ("alice", "bob"):
                route = bridge.routes[actor]
                path = route["object_root"] + "/pkg-01.bin"
                jobs = [
                    {"method": "GET", "path": path},
                    {"method": "HEAD", "path": path},
                    {"method": "GET", "path": path, "headers": {"Range": "bytes=0-0"}},
                    {"method": "GET", "path": path,
                     "headers": {"If-None-Match": '"deliberately-not-current"'}},
                ]
                records.append(bridge.batch(actor, jobs))
            closed = bridge.close()
            results.append({"run_id": spec["run_id"], "requests": records, "closed": closed})
        except Exception:
            bridge.force_stop()
            raise
        (output / f"{configuration}.json").write_text(json.dumps(results[-1], indent=2))
    (output / "summary.json").write_text(json.dumps({
        "scripted": True, "configurations": 3, "http_requests": 24,
        "model_calls": 0, "remote_audit_required": True,
        "source_freeze_sha256": gate.sha(HERE / "SOURCES-FROZEN.json")}, indent=2))


if __name__ == "__main__":
    main()
