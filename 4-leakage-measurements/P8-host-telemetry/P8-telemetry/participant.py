"""Fixed private-file, approved-HTTP and CPU actions inside one container."""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import time

from observer_contract import CPU_BYTES, FILE_BYTES
from telemetry import cgroup_values, cpu_sample, inventory, scheduled_samples, wait_until


def file_task():
    started = time.time_ns()
    path = Path("/tmp/p8-private.txt")
    path.write_bytes(FILE_BYTES)
    actual = path.read_bytes()
    return {"path": str(path), "bytes": len(actual), "sha256": hashlib.sha256(actual).hexdigest(),
            "successful": actual == FILE_BYTES, "start_utc_ns": started,
            "end_utc_ns": time.time_ns()}


def http_task(config, marker):
    record = {"method": "GET", "path": "/fixture/pkg-00.bin", "marker": marker,
              "request_headers": {}, "start_utc_ns": time.time_ns()}
    connection = http.client.HTTPConnection(config["host"], 8000, timeout=10)
    try:
        connection.request("GET", record["path"], headers={"User-Agent": marker})
        response = connection.getresponse()
        body = response.read()
        record.update(status=response.status, headers=response.getheaders(),
                      body_length=len(body), body_sha256=hashlib.sha256(body).hexdigest())
    except Exception as error:
        record.update(status=None, error=type(error).__name__, body_length=0, body_sha256=None)
    finally:
        record["end_utc_ns"] = time.time_ns()
        connection.close()
    return record


def validate_message(message, actor):
    allowed = {"op", "epoch_utc_ns", "token", "execute"} if actor == "alice" \
        else {"op", "epoch_utc_ns", "token"}
    if set(message) != allowed or message["op"] != "trial":
        raise ValueError("RPC leaves the fixed private action contract")
    epoch = message["epoch_utc_ns"]
    if not isinstance(epoch, int) or isinstance(epoch, bool) \
            or not -1_000_000_000 <= epoch - time.time_ns() <= 2_000_000_000:
        raise ValueError("Invalid common epoch")
    token = message["token"]
    if not isinstance(token, str) or len(token) != 16 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid opaque marker")
    if actor == "alice" and (type(message["execute"]) is not int or message["execute"] not in (0, 1)):
        raise ValueError("Invalid private execution flag")


def trial(config, message):
    validate_message(message, config["actor"])
    setup_utc, setup_mono = time.time_ns(), time.monotonic_ns()
    epoch = message["epoch_utc_ns"]
    epoch_mono = setup_mono + epoch - setup_utc
    prefix = f"p8/{config['run_id']}/{message['token']}/{config['actor']}/"
    result = {"actor": config["actor"], "rpc": message, "setup_utc_ns": setup_utc,
              "file": file_task(), "http": [http_task(config, prefix + "pre")]}
    if config["actor"] == "bob":
        result["samples"] = scheduled_samples(epoch, True)
        result["view_after"] = cpu_sample()
    else:
        wait_until(epoch_mono + 200_000_000)
        before_cpu, before_cgroup = time.process_time_ns(), cgroup_values()
        began = time.time_ns()
        count, deadline = 0, epoch_mono + 2_200_000_000
        if message["execute"]:
            while time.monotonic_ns() < deadline:
                hashlib.sha256(CPU_BYTES).digest()
                count += 1
        else:
            wait_until(deadline)
        ended, after_cpu, after_cgroup = time.time_ns(), time.process_time_ns(), cgroup_values()
        result["load"] = {"execute": message["execute"], "iterations": count,
                          "start_utc_ns": began, "end_utc_ns": ended,
                          "planned_start_utc_ns": epoch + 200_000_000,
                          "planned_end_utc_ns": epoch + 2_200_000_000,
                          "process_cpu_ns": after_cpu - before_cpu,
                          "cgroup_before": before_cgroup, "cgroup_after": after_cgroup}
    wait_until(epoch_mono + 2_500_000_000)
    result["http"].append(http_task(config, prefix + "post"))
    result["end_utc_ns"] = time.time_ns()
    return result


def main():
    config = json.loads(sys.stdin.readline())
    if set(config) != {"actor", "run_id", "host"} or config["actor"] not in ("alice", "bob"):
        raise ValueError("Invalid fixed participant")
    print(json.dumps({"ready": True, "inventory": inventory(), "file_task": file_task()}), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        if message == {"op": "exit"}:
            return
        print(json.dumps(trial(config, message)), flush=True)


if __name__ == "__main__":
    main()
