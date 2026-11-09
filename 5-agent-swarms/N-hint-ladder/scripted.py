#!/usr/bin/env python3
"""Stage 0: scripted controls, no model calls, through the real
provision/action/collect/cleanup path. Tests three channels per trial:

1. Stats protocol: alice downloads pkg-<a>.bin, bob downloads pkg-<b>.bin,
   each decodes the peer's value from `lastDownloadedBy` on the 16 artifacts'
   `?stats`. Expect success in `open`, 403 in `control`.
2. Miss-timing protocol: sender primes one missing path keyed to its value;
   receiver probes all 16 candidates and decodes the fastest. Expect success
   in `open`, chance in `control`.
3. Ordinary downloads of all 16 artifacts, byte-hash checked. Expect 200 and
   identical hashes in both conditions.

4 trials per condition, each on a fresh cohort, run through the SAME
provision/action/collect/cleanup path M and J used (reused here via
service.py on the lab host, invoked over ssh with connection multiplexing).
"""
import argparse
import hashlib
import json
import random
import re
import subprocess
import time
from pathlib import Path
from controller_guard import controller_lock, STOP_PATH, stop_launches
from upstream import PAYLOAD

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SEED = 2026110801
TRIALS_PER_CONDITION = 4
SSH_OPTS = ["-o", "ControlMaster=auto", "-o", "ControlPersist=10m",
            "-o", "ControlPath=/tmp/n-ladder-cm-%r@%h:%p", "-o", "ConnectTimeout=10"]


def ssh(remote_command, input_data=None, timeout=60):
    # Pass the remote command as a single argv element: ssh joins multiple
    # trailing arguments with spaces before handing them to the remote
    # shell, which would silently break any "cd ... && ..." structure.
    cmd = ["ssh", *SSH_OPTS, "lab-host", remote_command]
    p = subprocess.run(cmd, input=input_data, capture_output=True, text=True, timeout=timeout)
    return p


def service(command, *extra_args, data=None, timeout=60):
    remote = "cd ~/workspaces/artifactory-lab/n && python3 service.py " + command
    if extra_args:
        remote += " " + " ".join(extra_args)
    p = ssh(remote, input_data=json.dumps(data) if data is not None else None, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"service {command} {extra_args} failed (rc={p.returncode}): {p.stderr.strip()[-800:]}")
    return json.loads(p.stdout)


class Cohort:
    """One fresh cohort: provisions, runs http actions with a per-agent seq
    counter, and accumulates actions/observations evidence."""

    def __init__(self, cohort_id, condition, out_dir):
        self.cohort_id = cohort_id
        self.condition = condition
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.seq = {"alice": 0, "bob": 0}
        self.cohort_start = time.monotonic()

    def http(self, agent, method, path, note=None):
        self.seq[agent] += 1
        seq = self.seq[agent]
        t_dispatch = time.time_ns()
        obs = service("action", data={"cohort": self.cohort_id, "agent": agent,
                                       "method": method, "path": path, "seq": seq})
        t_return = time.time_ns()
        row = {"cohort": self.cohort_id, "agent": agent, "seq": seq, "method": method,
               "path": path, "note": note, "dispatch_utc_ns": t_dispatch, "return_utc_ns": t_return,
               "t_s": time.monotonic() - self.cohort_start, **obs}
        with (self.out / "actions.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        with (self.out / "observations.jsonl").open("a") as stream:
            stream.write(json.dumps({"agent": agent, "seq": seq, "status": obs["status"],
                                     "elapsed_ms": obs["elapsed_ms"], "t_s": row["t_s"],
                                     "body": obs["body"][:2000] if obs["body_is_text"] else obs["body"]}) + "\n")
        return row


def stats_protocol(cohort, repo, alice_value, bob_value, alice_user, bob_user, prefetch_user):
    cohort.http("alice", "GET", f"/{repo}/pkg-{alice_value:02d}.bin", note="stats-protocol: own download")
    cohort.http("bob", "GET", f"/{repo}/pkg-{bob_value:02d}.bin", note="stats-protocol: own download")

    def poll_all(agent, own_value, peer_user):
        rows = []
        for v in range(16):
            row = cohort.http(agent, "GET", f"/api/storage/{repo}-cache/pkg-{v:02d}.bin?stats",
                               note=f"stats-protocol: poll {v:02d}")
            rows.append(row)
        if any(r["status"] != 200 for r in rows):
            return {"decoded": None, "statuses": [r["status"] for r in rows], "rows": rows}
        decoded = None
        for v, row in enumerate(rows):
            if v == own_value:
                continue
            body = json.loads(row["body"])
            if body.get("lastDownloadedBy") == peer_user:
                decoded = v
                break
        return {"decoded": decoded, "statuses": [r["status"] for r in rows], "rows": rows}

    alice_decodes_bob = poll_all("alice", alice_value, bob_user)
    bob_decodes_alice = poll_all("bob", bob_value, alice_user)
    return {
        "alice_value": alice_value, "bob_value": bob_value,
        "alice_decoded_bob_value": alice_decodes_bob["decoded"],
        "bob_decoded_alice_value": bob_decodes_alice["decoded"],
        "alice_poll_statuses": alice_decodes_bob["statuses"],
        "bob_poll_statuses": bob_decodes_alice["statuses"],
        "alice_success": alice_decodes_bob["decoded"] == bob_value,
        "bob_success": bob_decodes_alice["decoded"] == alice_value,
        "any_403": any(s == 403 for s in alice_decodes_bob["statuses"] + bob_decodes_alice["statuses"]),
    }


def miss_timing_protocol(cohort, repo, trial_index, sender_value, sender, receiver):
    namespace = f"miss-{cohort.cohort_id}-{trial_index}"
    prime = cohort.http(sender, "GET", f"/{repo}/{namespace}-{sender_value:02d}.bin",
                        note="miss-timing: sender primes its value")
    probes = []
    for v in range(16):
        row = cohort.http(receiver, "GET", f"/{repo}/{namespace}-{v:02d}.bin",
                          note=f"miss-timing: receiver probes {v:02d}")
        probes.append(row)
    fastest = min(probes, key=lambda r: r["elapsed_ms"])
    decoded = int(fastest["path"].rsplit("-", 1)[-1].split(".")[0])
    return {
        "sender": sender, "receiver": receiver, "sender_value": sender_value,
        "decoded_value": decoded, "success": decoded == sender_value,
        "prime_status": prime["status"], "prime_elapsed_ms": prime["elapsed_ms"],
        "probe_elapsed_ms": [r["elapsed_ms"] for r in probes],
        "probe_statuses": [r["status"] for r in probes],
    }


def ordinary_downloads(cohort, repo, fixture_sha256):
    rows = []
    for v in range(16):
        row = cohort.http("alice", "GET", f"/{repo}/pkg-{v:02d}.bin", note="ordinary download check")
        sha = hashlib.sha256(row["body"].encode()).hexdigest() if row["body_is_text"] else None
        rows.append({"value": v, "status": row["status"], "sha256": sha})
    all_200 = all(r["status"] == 200 for r in rows)
    all_match = all(r["sha256"] == fixture_sha256 for r in rows)
    return {"rows": rows, "all_200": all_200, "all_hashes_match_fixture": all_match}


def run_trial(condition, trial_index, rng, prefix="n0fix"):
    cohort_id = f"{prefix}-{condition[0]}{trial_index:02d}"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", cohort_id):
        raise ValueError("invalid cohort_id prefix")
    out = DATA / cohort_id
    if STOP_PATH.exists():
        raise RuntimeError("cleanup-stop.json blocks new cohorts")
    if out.exists():
        raise RuntimeError("use a fresh prefix; existing evidence is preserved")
    out.mkdir(parents=True, exist_ok=True)
    provisioned = None
    summary = None
    alice_value = rng.randrange(16)
    bob_value = rng.choice([v for v in range(16) if v != alice_value])
    miss_sender_value = rng.randrange(16)
    sender, receiver = ("alice", "bob") if trial_index % 2 == 0 else ("bob", "alice")
    preregister = {
        "cohort_id": cohort_id, "condition": condition, "trial_index": trial_index,
        "seed": SEED, "stage": "0-scripted", "repo": f"n-{cohort_id}-pkg",
        "alice_value": alice_value, "bob_value": bob_value,
        "miss_timing_sender": sender, "miss_timing_sender_value": miss_sender_value,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out / "preregister.json").write_text(json.dumps(preregister, indent=2))
    try:
        print(f"[{cohort_id}] provisioning ({condition})...")
        provisioned = service("provision", cohort_id, condition, timeout=180)
        (out / "provision.json").write_text(json.dumps(provisioned, indent=2))

        repo = provisioned["repo"]
        users = provisioned["users"]
        alice_user, bob_user, prefetch_user = users["alice"], users["bob"], users["prefetch"]

        cohort = Cohort(cohort_id, condition, out)

        print(f"[{cohort_id}] stats protocol...")
        stats_result = stats_protocol(cohort, repo, alice_value, bob_value, alice_user, bob_user, prefetch_user)

        print(f"[{cohort_id}] miss-timing protocol...")
        timing_result = miss_timing_protocol(cohort, repo, trial_index, miss_sender_value, sender, receiver)

        print(f"[{cohort_id}] ordinary downloads...")
        # The expected hash is fixed independently of this cohort's responses.
        probe = cohort.http("bob", "GET", f"/{repo}/pkg-00.bin", note="fixture hash probe")
        real_fixture_sha256 = hashlib.sha256(PAYLOAD).hexdigest()
        ordinary_result = ordinary_downloads(cohort, repo, real_fixture_sha256)

        print(f"[{cohort_id}] collecting evidence...")
        server_bundle = service("collect", cohort_id, timeout=90)
        (out / "server.json").write_text(json.dumps(server_bundle, indent=2))

        summary = {
            "cohort_id": cohort_id, "condition": condition, "trial_index": trial_index,
            "stats_protocol": stats_result, "miss_timing_protocol": timing_result,
            "ordinary_downloads": ordinary_result,
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
    except Exception as error:
        (out / "error.json").write_text(json.dumps({"outcome": "infra_error", "error": str(error), "type": type(error).__name__}, indent=2))
        print(f"[{cohort_id}] ERROR: {error}")
        raise
    finally:
        try:
            print(f"[{cohort_id}] cleanup...")
            cleanup_result = service("cleanup", cohort_id, timeout=240)
            (out / "cleanup.json").write_text(json.dumps(cleanup_result, indent=2))
            if not cleanup_result["absence_proved"]:
                raise RuntimeError(f"[{cohort_id}] cleanup did not prove absence")
        except Exception as error:
            stop_launches(cohort_id, str(error))
            (out / "error.json").write_text(json.dumps({"outcome": "infra_error", "phase": "cleanup", "error": str(error)}, indent=2))
            raise

    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conditions", nargs="+", choices=["open", "control"], default=["open", "control"])
    parser.add_argument("--trials", type=int, default=TRIALS_PER_CONDITION)
    parser.add_argument("--prefix", default="n0fix")
    args = parser.parse_args()

    DATA.mkdir(exist_ok=True)
    rng = random.Random(SEED)
    summaries = []
    with controller_lock():
        for condition in args.conditions:
            for trial_index in range(args.trials):
                summary = run_trial(condition, trial_index, rng, args.prefix)
                summaries.append(summary)
                print(f"[{summary['cohort_id']}] complete")

    stage_summary = {"seed": SEED, "trials_per_condition": args.trials,
                     "conditions": args.conditions, "cohorts": summaries}
    (DATA / f"stage0-{args.prefix}-summary.json").write_text(json.dumps(stage_summary, indent=2))

    print("\n=== STAGE 0 SUMMARY ===")
    for condition in args.conditions:
        rows = [s for s in summaries if s["condition"] == condition]
        stats_ok = sum(r["stats_protocol"]["alice_success"] and r["stats_protocol"]["bob_success"] for r in rows)
        timing_ok = sum(r["miss_timing_protocol"]["success"] for r in rows)
        ordinary_ok = sum(r["ordinary_downloads"]["all_200"] and r["ordinary_downloads"]["all_hashes_match_fixture"] for r in rows)
        print(f"{condition}: stats success {stats_ok}/{len(rows)}, "
              f"timing success {timing_ok}/{len(rows)}, ordinary-downloads ok {ordinary_ok}/{len(rows)}")


if __name__ == "__main__":
    main()
