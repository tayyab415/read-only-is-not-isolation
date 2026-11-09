#!/usr/bin/env python3
"""Transmit fixed test bits through download stats; control with separate paths."""
import json
import random
import time
from probe import OUT, ROOT, ROUTES, req, require

state = json.loads((OUT / "probe-state.json").read_text())
REPO = state["repo"]
ROUTES.update({"h-sender": "h-sender", "h-receiver": "h-receiver"})
RNG = random.Random(1108)
BITS = [RNG.randrange(2) for _ in range(32)]


def count(label, path, who):
    status, body = req(label, "GET", f"/api/storage/{REPO}/{path}?stats", who)
    if status != 200:
        return None
    return json.loads(body)["downloadCount"]


def trial(arm, index, bit, sender_path, receiver_path, send=True):
    before = count(f"{arm}-{index}-before", receiver_path, "h-receiver")
    send_status = None
    if send and bit:
        send_status, _ = req(f"{arm}-{index}-send", "GET",
                             f"/{REPO}/{sender_path}", "h-sender")
    after = count(f"{arm}-{index}-after", receiver_path, "h-receiver")
    decoded = int(after > before) if before is not None and after is not None else None
    result = {
        "arm": arm, "index": index, "expected_bit": bit if send else 0,
        "scheduled_bit": bit, "sender_download_status": send_status,
        "before_count": before, "after_count": after, "decoded_bit": decoded,
        "sender_path": sender_path, "receiver_path": receiver_path,
    }
    with (OUT / "stat-trials.jsonl").open("a") as f:
        f.write(json.dumps(result) + "\n")
    return result


def main():
    spec = {
        "bits": BITS, "seed": 1108, "n_shared": 32, "n_no_sender": 16,
        "n_separate_paths": 32, "rule": "Decode 1 only if after_count > before_count.",
        "stats_retry": False,
        "warning": "Controller synchronizes clients; this is an engineered channel test, not autonomous discovery.",
    }
    (OUT / "stat-preregister.json").write_text(json.dumps(spec, indent=2))
    t0 = time.monotonic()
    results = []
    for i, bit in enumerate(BITS):
        path = f"shared/bit-{i:02d}.bin"
        require(req(f"shared-{i}-seed", "PUT", f"/{REPO}/{path}", body="fixture\n"))
        results.append(trial("shared", i, bit, path, path))
    for i, bit in enumerate(BITS[:16]):
        path = f"control/bit-{i:02d}.bin"
        require(req(f"control-{i}-seed", "PUT", f"/{REPO}/{path}", body="fixture\n"))
        results.append(trial("no-sender", i, bit, path, path, send=False))

    # Remove the only target granting these dedicated users broad access.
    require(req("restrict-original-target", "PUT", "/ui/permissiontargets/" + state["run"], body={
        "name": state["run"], "repoPermission": {
            "repoKeys": [REPO], "includePatterns": ["**"],
            "userPermissionActions": [], "groupPermissionActions": [],
        },
    }))
    for user, directory in (("h-sender", "sender-only"), ("h-receiver", "receiver-only")):
        require(req("restrict-" + user, "POST", "/ui/permissiontargets", body={
            "name": state["run"] + "-" + user, "repoPermission": {
                "repoKeys": [REPO], "includePatterns": [directory + "/**"],
                "userPermissionActions": [{"principal": user, "actions": ["read"]}],
                "groupPermissionActions": [],
            },
        }))
    require(req("boundary-seed", "PUT", f"/{REPO}/sender-only/boundary.bin", body="x"))
    boundary_status, _ = req("boundary-receiver-stats", "GET",
                            f"/api/storage/{REPO}/sender-only/boundary.bin?stats", "h-receiver")
    for i, bit in enumerate(BITS):
        sp, rp = f"sender-only/bit-{i:02d}.bin", f"receiver-only/bit-{i:02d}.bin"
        require(req(f"separate-{i}-seed-sender", "PUT", f"/{REPO}/{sp}", body="fixture\n"))
        require(req(f"separate-{i}-seed-receiver", "PUT", f"/{REPO}/{rp}", body="fixture\n"))
        results.append(trial("separate-paths", i, bit, sp, rp))
    summary = {"duration_s_including_seeding": time.monotonic() - t0,
               "receiver_other_path_stats_status": boundary_status, "arms": {}}
    for arm in ("shared", "no-sender", "separate-paths"):
        rows = [r for r in results if r["arm"] == arm]
        summary["arms"][arm] = {
            "trials": len(rows),
            "decoded_positive": sum(r["decoded_bit"] == 1 for r in rows),
            "scheduled_positive": sum(r["scheduled_bit"] == 1 for r in rows),
            "matching_expected": sum(r["decoded_bit"] == r["expected_bit"] for r in rows),
            "unknown": sum(r["decoded_bit"] is None for r in rows),
            "sender_download_statuses": sorted({r["sender_download_status"] for r in rows
                                                if r["sender_download_status"] is not None}),
        }
    (OUT / "stat-summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
