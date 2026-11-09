"""Validate all background commands, then reuse the frozen native wire auditor."""
import json
from pathlib import Path
import shutil
import tempfile

import background
import p5_gate
import audit_v2 as p4_audit


def check_remote(folder, expected_source_sha256):
    failures, checks = [], 0

    def check(name, value):
        nonlocal checks
        checks += 1
        if not value:
            failures.append(name)

    sealed = json.loads((folder / "sealed.json").read_text())
    public = json.loads((folder / "provision.json").read_text())
    data = p4_audit.rows(folder / "actions.jsonl") if (folder / "actions.jsonl").exists() else []
    check("full original seal", sealed["complete"]
          and sealed["source_freeze_sha256"] == expected_source_sha256
          and all(p5_gate.sha(folder / name) == expected for name, expected in sealed["file_sha256"].items()))
    active = sealed["background_active"]
    check("private assignment", isinstance(active, int) and not isinstance(active, bool) and active in (0, 1))
    shadow = [row for row in data if row["actor"] == "bob"]
    check("all four background waves", len(shadow) == 4)
    route = background.deployment.route(public, "bob")
    wanted = background.jobs(route)
    for index, row in enumerate(shadow):
        timing = row["request"]["background_timing"]
        check(f"wave {index} fixed command schedule", row["complete"]
              and row["request"]["requests"] == wanted
              and timing["wave"] == index and timing["planned_offset_s"] == background.WAVE_OFFSETS_S[index]
              and timing["dispatch_monotonic_ns"] >= timing["phase_epoch_monotonic_ns"]
              + int(background.WAVE_OFFSETS_S[index] * 1e9))
        check(f"wave {index} executed HTTP denominator", len(row["records"]) == (16 if active else 0))
    check("one background epoch", len({row["request"]["background_timing"]["phase_epoch_monotonic_ns"]
                                       for row in shadow}) == 1)
    check("all batch and request records retained", sealed["batches"] == len(data)
          and sealed["http_requests"] == sum(len(row["records"]) for row in data)
          and len({row["id"] for row in data}) == len(data)
          and all(row["complete"] for row in data)
          and all(row["actor"] in ("alice", "bob") for row in data))
    check("model requests cannot disappear from the projection",
          all(1 <= len(row["records"]) == len(row["request"]["requests"]) <= 16
              for row in data if row["actor"] == "alice")
          and sum(row["actor"] == "alice" for row in data) <= 12)
    if failures:
        return {"passed": False, "checks": checks, "failures": failures, "failure_count": len(failures)}
    # Idle RPCs remain in the original evidence and the checks above. Their
    # declared zero HTTP work is absent only from this native-wire projection.
    # No failed observation is dropped, and original hashes are checked first.
    wire_batches = [row for row in data if row["records"]]
    with tempfile.TemporaryDirectory(prefix="p5-native-audit-") as temporary:
        projection = Path(temporary)
        for name in ("provision.json", "server.json", "server-log-lines.txt", "cleanup.json"):
            shutil.copyfile(folder / name, projection / name)
        (projection / "actions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in wire_batches))
        projected_seal = {**sealed, "batches": len(wire_batches),
                          "file_sha256": {file.name: p5_gate.sha(file)
                                          for file in projection.iterdir() if file.is_file()}}
        (projection / "sealed.json").write_text(json.dumps(projected_seal))
        native = p4_audit.audit_remote(projection, expected_source_sha256)
    return {"passed": native["passed"], "checks": checks + native["checks"],
            "failure_count": native["failure_count"], "failures": native["failures"],
            "native_result": native, "run_id": public["run_id"], "scripted": sealed["scripted"],
            "background_active": active, "background_commands": 64,
            "background_http_requests": 64 if active else 0,
            "http_requests": sealed["http_requests"],
            "original_seal_sha256": p5_gate.sha(folder / "sealed.json"),
            "scope": "Complete matched background commands and unchanged native serving evidence checks."}
