#!/usr/bin/env python3
"""Preregistered challenges after the main matrix. No decoder retuning."""
import concurrent.futures
import json
import os
import random
import time
from campaign import ROOT, STATE, Requests, append, save, timing_trial


def main():
    state = json.loads(STATE.read_text())
    q = Requests(state)
    if not (q.out / "run-finished.json").exists():
        raise RuntimeError("Wait for the verified main process to finish")
    plan_path = q.out / "followup-preregister.json"
    if plan_path.exists():
        raise RuntimeError("Followup already started; inspect before continuing")
    rng = random.Random(2026110802)

    def bits(n):
        values = [0] * (n // 2) + [1] * (n // 2)
        rng.shuffle(values)
        return values

    plan = {
        "seed": 2026110802, "created_utc_ns": time.time_ns(),
        "load_bits": {arm: bits(32) for arm in ("method-only", "both")},
        "load_delay_ms": 20, "noise_workers": 2, "noise_requests_per_worker": 160,
        "noise_pause_s": 0.1, "denied_prime_bits": bits(16),
        "counter_bits": {method: bits(16) for method in ("HEAD", "conditional-GET", "range-GET")},
        "thresholds": json.loads((q.out / "frozen-thresholds.json").read_text()),
        "note": "Exploratory followups, fixed before their own requests. Main findings selected these questions.",
    }
    save(plan_path, plan)
    save(q.out / "followup-start.json", {"utc_ns": time.time_ns(), "pid": os.getpid()})
    baseline = state["repos"]["method-only"]["sender"]
    q.required("noise-prewarm", "GET", f"/{baseline}/present/noise.bin", "prefetch")

    def noise(role):
        for i in range(plan["noise_requests_per_worker"]):
            q.required(f"noise-{role}-{i}", "GET", f"/{baseline}/present/noise.bin", role)
            time.sleep(plan["noise_pause_s"])

    started = time.time_ns()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(noise, role) for role in ("sender", "receiver")]
        for arm, schedule in plan["load_bits"].items():
            for i, bit in enumerate(schedule):
                row = timing_trial(q, state, arm, 20, i, bit, "load")
                row["decoded_bit"] = int(row["duration_ns"] < plan["thresholds"]["20"]["threshold_ns"])
                append(q.out / "followup-timing.jsonl", row)
            print("Load arm done:", arm, flush=True)
        for job in jobs:
            job.result()
    save(q.out / "noise-window.json", {"start_utc_ns": started, "end_utc_ns": time.time_ns()})

    receiver_repo = state["repos"]["partitioned"]["receiver"]
    for i, bit in enumerate(plan["denied_prime_bits"]):
        relative = f'missing/150/{state["run"]}-denied-prime-{i}.bin'
        sender = None
        if bit:
            sender, _ = q.request(f"denied-{i}-prime", "GET",
                                  f"/{receiver_repo}/{relative}", "sender")
            if sender["status"] not in (401, 403):
                raise RuntimeError("Cross-scope request unexpectedly allowed")
        receiver, _ = q.request(f"denied-{i}-receive", "GET",
                                f"/{receiver_repo}/{relative}", "receiver")
        append(q.out / "followup-timing.jsonl", {
            "arm": "partitioned", "delay_ms": 150, "index": i, "phase": "denied-prime",
            "expected_bit": bit, "scheduled_bit": bit, "relative_path": relative,
            "sender_label": sender["label"] if sender else None,
            "sender_status": sender["status"] if sender else None,
            "receiver_label": receiver["label"], "duration_ns": receiver["duration_ns"],
            "status": receiver["status"], "decoded_bit": int(
                receiver["duration_ns"] < plan["thresholds"]["150"]["threshold_ns"]),
        })
    print("Denied-prime control done", flush=True)

    _, body = q.required("counter-etag-read", "GET", f"/{baseline}/present/common.bin", "sender")
    records = [json.loads(line) for line in (q.out / "requests.jsonl").read_text().splitlines()]
    etag_record = next(row for row in records if row["label"] == "counter-etag-read")
    etag = next((value for key, value in etag_record["headers"].items() if key.lower() == "etag"), None)
    if not etag:
        raise RuntimeError("No ETag, retain failure rather than claim a conditional test")
    stat_path = f"/api/storage/{baseline}-cache/present/common.bin?stats"
    for mode, schedule in plan["counter_bits"].items():
        for i, bit in enumerate(schedule):
            before, text_before = q.required(f"counter-{mode}-{i}-before", "GET", stat_path, "receiver")
            sent = None
            if bit:
                method, headers = ("HEAD", {}) if mode == "HEAD" else (
                    "GET", {"If-None-Match": etag} if mode == "conditional-GET" else {"Range": "bytes=0-0"})
                # Direct authenticated host transport preserves Range. Gateway intentionally
                # forwards only the headers specified in its original implementation.
                sent, _ = q.request(f"counter-{mode}-{i}-sender", method,
                                    f"/{baseline}/present/common.bin", "sender",
                                    direct=mode == "range-GET", headers=headers)
            after, text_after = q.required(f"counter-{mode}-{i}-after", "GET", stat_path, "receiver")
            a, b = json.loads(text_before)["downloadCount"], json.loads(text_after)["downloadCount"]
            append(q.out / "counter-followup.jsonl", {
                "mode": mode, "index": i, "expected_bit": bit, "decoded_bit": int(b > a),
                "before_count": a, "after_count": b, "before_label": before["label"],
                "after_label": after["label"], "sender_label": sent["label"] if sent else None,
                "sender_status": sent["status"] if sent else None,
                "sender_body_bytes": sent["body_bytes"] if sent else None,
                "transport": sent["transport"] if sent else None,
            })
        print("Counter method done:", mode, flush=True)
    save(q.out / "followup-finished.json", {"utc_ns": time.time_ns(), "status": "complete"})
    print("FOLLOWUP COMPLETE", flush=True)


if __name__ == "__main__":
    main()
