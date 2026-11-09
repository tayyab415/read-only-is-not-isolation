"""Fixed procfs/cgroup readers. No arbitrary path or command input."""
import hashlib
import os
from pathlib import Path
import time

from observer_contract import OFFSETS, parse_cpu


def cgroup_values():
    text = Path("/sys/fs/cgroup/cpu.stat").read_text()
    values = dict((key, int(value)) for key, value in (line.split() for line in text.splitlines()))
    if "usage_usec" not in values:
        raise RuntimeError("P8 requires cgroup v2 accounting")
    return {"raw": text, "values": values}


def cpu_sample():
    started = time.time_ns()
    data = Path("/proc/stat").read_bytes()
    line = data.splitlines()[0].decode("ascii")
    parse_cpu(line)
    return {"read_start_utc_ns": started, "read_end_utc_ns": time.time_ns(),
            "cpu_line": line, "file_sha256": hashlib.sha256(data).hexdigest()}


def wait_until(monotonic_ns):
    remaining = (monotonic_ns - time.monotonic_ns()) / 1e9
    if remaining > 0:
        time.sleep(remaining)


def scheduled_samples(epoch_utc_ns, include_cgroup):
    setup_utc, setup_mono = time.time_ns(), time.monotonic_ns()
    epoch_mono = setup_mono + epoch_utc_ns - setup_utc
    output = []
    for offset in OFFSETS:
        deadline = epoch_mono + round(offset * 1e9)
        wait_until(deadline)
        sample = cpu_sample()
        if include_cgroup:
            sample["cgroup"] = cgroup_values()
        sample.update(scheduled_offset_s=offset, schedule_epoch_utc_ns=epoch_utc_ns,
                      schedule_setup_utc_ns=setup_utc,
                      start_lag_ms=max(0, (sample["read_start_utc_ns"] -
                                           epoch_utc_ns - round(offset * 1e9)) / 1e6))
        output.append(sample)
    return output


def inventory():
    root = Path("/sys/fs/cgroup")
    result = {"kernel": os.uname()._asdict() if hasattr(os.uname(), "_asdict") else
              dict(zip(("sysname", "nodename", "release", "version", "machine"), os.uname())),
              "cpu_count": os.cpu_count(), "clock_ticks": os.sysconf("SC_CLK_TCK"),
              "cpu_stat": cpu_sample(),
              "namespace_ids": {kind: os.readlink(f"/proc/self/ns/{kind}")
                                for kind in ("cgroup", "net", "pid", "mnt")},
              "mountinfo": Path("/proc/self/mountinfo").read_text(),
              "self_cgroup": Path("/proc/self/cgroup").read_text(),
              "cgroup_v2": (root / "cgroup.controllers").exists(),
              "cpu_max": (root / "cpu.max").read_text() if (root / "cpu.max").exists() else None,
              "memory_max": (root / "memory.max").read_text() if (root / "memory.max").exists() else None,
              "cgroup": cgroup_values()}
    return result
