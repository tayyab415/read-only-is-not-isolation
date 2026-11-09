#!/usr/bin/env python3
"""Six matched background smoke fixtures, with no model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import p5_gate
from ordinary_run import Bridge, background_loop

HERE = Path(__file__).resolve().parent


def main():
    p5_gate.sources()
    folder = HERE / "smoke-controller"
    folder.mkdir(exist_ok=False)
    for configuration in ("open", "separate", "immutable"):
        for active in (0, 1):
            spec = {"run_id": f"p5b-s1-{configuration}-{active}", "configuration": configuration,
                    "background_active": active}
            bridge = Bridge(spec, smoke=True)
            try:
                phase = time.monotonic_ns() + 200_000_000
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(background_loop, bridge, folder / f"{spec['run_id']}-background.jsonl",
                                         phase)
                    root = bridge.routes["alice"]["object_root"]
                    jobs = [{"method": "GET", "path": f"{root}/pkg-{slot:02d}.bin",
                             "headers": {}, "offset_s": index * .1}
                            for index, slot in enumerate((1, 4, 9, 12, 1, 4, 9, 12))]
                    observed = bridge.batch("alice", jobs)
                    shadow = future.result()
                closed = bridge.close()
                (folder / f"{spec['run_id']}.json").write_text(json.dumps({
                    "scripted": True, "spec": spec, "observed": observed,
                    "background": shadow, "closed": closed}, indent=2))
            except Exception:
                bridge.force_stop()
                raise
    (folder / "summary.json").write_text(json.dumps({
        "scripted": True, "configurations": 3, "activity_conditions": 2,
        "background_commands": 384, "background_http_requests": 192,
        "diagnostic_http_requests": 48, "model_calls": 0,
        "independent_remote_audit_required": True}, indent=2))


if __name__ == "__main__":
    main()
