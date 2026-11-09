#!/usr/bin/env python3
"""Run the P1 smoke sequence on the labhost. Calibration and holdout are refused."""
import argparse
import json
import random
import shutil
import time
from pathlib import Path

import service

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "smoke"
RECIPES = json.loads((ROOT / "recipes.json").read_text())
SEED = RECIPES["seed"]
WINDOW = RECIPES["window_seconds"]
SETTLE = RECIPES["settle_seconds"]
GAP = RECIPES["inter_trial_seconds"]
POST_ARTIFACTS = (0, 1, 2, 3)


class Trial:
    def __init__(self, run_id, trial_number, state):
        self.run_id = run_id
        self.trial_id = f"t{trial_number:02d}"
        self.state = state
        self.sequence = {"alice": 0, "bob": 0}
        self.rows = []

    def request(self, actor, method, path, headers=None, note=None, target="gateway"):
        self.sequence[actor] += 1
        row = service.request(self.run_id, actor, self.trial_id, self.sequence[actor],
                              method, path, headers, note, target)
        self.rows.append(row)
        return row

    def burst(self, actor, path):
        first = self.sequence[actor] + 1
        rows = service.burst(self.run_id, actor, self.trial_id, first, path,
                             RECIPES["load_concurrency"], "sender: load burst")
        self.sequence[actor] += len(rows)
        self.rows.extend(rows)
        return rows


def repo_for(state, actor):
    return state["repositories"].get(actor) or state["repositories"]["shared"]


def choices_for(state):
    key = "storage_blocked_recipe_probabilities" if state["storage_blocked"] else "recipe_probabilities"
    return list(RECIPES[key].items())


def choose_recipe(state, rng):
    candidates = choices_for(state)
    draw = rng.random()
    total = 0
    for recipe, probability in candidates:
        total += probability
        if draw <= total:
            return recipe
    return candidates[-1][0]


def metadata_snapshot(trial, receiver):
    repo = repo_for(trial.state, receiver)
    for value in POST_ARTIFACTS:
        trial.request(receiver, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?stats",
                      note="pre-snapshot: stats")
        trial.request(receiver, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?properties",
                      note="pre-snapshot: properties")
        trial.request(receiver, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?list",
                      note="pre-snapshot: list")


def sender_recipe(trial, sender, recipe, rng, sham):
    repo = repo_for(trial.state, sender)
    value = rng.randrange(16)
    rendezvous = f"/{repo}/rendezvous/{trial.trial_id}-get.bin"
    fresh = f"/{repo}/fresh/{trial.trial_id}-{rng.getrandbits(48):012x}.bin"
    if sham:
        target_path = f"/sham/{recipe}/{trial.trial_id}"
        method = "HEAD" if recipe.endswith("head") else "GET"
        trial.request(sender, method, target_path, note=f"sender: sham {recipe}", target="dummy")
        return {"recipe": recipe, "target": "dummy", "requests": 1}
    if recipe == "artifact_get":
        trial.request(sender, "GET", f"/{repo}/pkg-{value:02d}.bin", note="sender: artifact GET")
    elif recipe == "artifact_head":
        trial.request(sender, "HEAD", f"/{repo}/pkg-{value:02d}.bin", note="sender: artifact HEAD")
    elif recipe == "artifact_conditional_get":
        trial.request(sender, "GET", f"/{repo}/pkg-{value:02d}.bin",
                      headers={"If-None-Match": trial.state["fixture_etag"]},
                      note="sender: conditional artifact GET")
    elif recipe == "artifact_range_get":
        trial.request(sender, "GET", f"/{repo}/pkg-{value:02d}.bin",
                      headers={"Range": "bytes=0-0"}, note="sender: one-byte range GET")
    elif recipe == "artifact_stats":
        trial.request(sender, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?stats",
                      note="sender: stats")
    elif recipe == "artifact_properties":
        trial.request(sender, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?properties",
                      note="sender: properties")
    elif recipe == "artifact_list":
        trial.request(sender, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?list",
                      note="sender: list")
    elif recipe == "rendezvous_get":
        trial.request(sender, "GET", rendezvous, note="sender: rendezvous GET")
    elif recipe == "directory_get":
        trial.request(sender, "GET", f"/{repo}?list", note="sender: directory listing")
    elif recipe == "fresh_get":
        trial.request(sender, "GET", fresh, note="sender: random fresh GET")
    elif recipe == "load":
        deadline = time.monotonic() + WINDOW
        count = 0
        while time.monotonic() < deadline:
            trial.burst(sender, f"/{repo}/pkg-{value:02d}.bin")
            count += RECIPES["load_concurrency"]
        return {"recipe": recipe, "target": f"pkg-{value:02d}.bin", "requests": count}
    else:
        raise RuntimeError("unknown recipe")
    return {"recipe": recipe, "target": repo, "requests": 1}


def post_battery(trial, receiver):
    repo = repo_for(trial.state, receiver)
    for value in POST_ARTIFACTS:
        trial.request(receiver, "GET", f"/{repo}/pkg-{value:02d}.bin", note="post: artifact GET")
        trial.request(receiver, "HEAD", f"/{repo}/pkg-{value:02d}.bin", note="post: artifact HEAD")
        trial.request(receiver, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?stats", note="post: stats")
        trial.request(receiver, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?properties", note="post: properties")
        trial.request(receiver, "GET", f"/api/storage/{repo}-cache/pkg-{value:02d}.bin?list", note="post: list")
    trial.request(receiver, "GET", f"/{repo}/rendezvous/{trial.trial_id}-get.bin", note="post: first-touch rendezvous GET")
    trial.request(receiver, "HEAD", f"/{repo}/rendezvous/{trial.trial_id}-head.bin", note="post: first-touch rendezvous HEAD")
    trial.request(receiver, "GET", f"/{repo}?list", note="post: directory listing")
    for _ in range(5):
        trial.request(receiver, "GET", f"/{repo}/pkg-00.bin", note="post: timed cached GET")


def write_joined(out_dir, server):
    gateway = {}
    for row in server["gateway_log"]:
        marker = row.get("marker")
        if marker:
            gateway.setdefault(marker, []).append(row)
    art = {}
    for row in server["artifactory_router_log"]:
        raw = json.dumps(row, sort_keys=True)
        for client in server["client_actions"]:
            if client["marker"] in raw:
                art.setdefault(client["marker"], []).append(row)
    request_lines = server["artifactory_request_log_lines"]
    joined = []
    for client in server["client_actions"]:
        marker = client["marker"]
        joined.append({
            "marker": marker, "trial_id": client["trial_id"], "client": client,
            "gateway": gateway.get(marker, []), "artifactory_router": art.get(marker, []),
            "artifactory_request_lines": [line for line in request_lines if marker in line],
        })
    with (out_dir / "joined.jsonl").open("w") as stream:
        for row in joined:
            stream.write(json.dumps(row) + "\n")
    counts = {
        "client_records": len(joined),
        "gateway_records": sum(len(row["gateway"]) for row in joined),
        "artifactory_router_records": sum(len(row["artifactory_router"]) for row in joined),
        "artifactory_request_records": sum(len(row["artifactory_request_lines"]) for row in joined),
        "client_markers_without_gateway": [row["marker"] for row in joined if len(row["gateway"]) != 1],
        "forwarded_markers_without_router": [
            row["marker"] for row in joined
            if row["gateway"] and row["gateway"][0]["policy_reason"] is None and not row["artifactory_router"]
        ],
    }
    (out_dir / "join-counts.json").write_text(json.dumps(counts, indent=2))
    return counts


def run_scenario(scenario, configuration, trials, rng):
    run_id = f"smoke-r1-{scenario}"
    out_dir = DATA / scenario
    if out_dir.exists():
        raise RuntimeError(f"smoke output already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    with_dummy = scenario == "sham"
    provisioned = False
    started = time.time_ns()
    schedule = [0] * (trials // 2) + [1] * (trials // 2)
    rng.shuffle(schedule)
    trial_rows = []
    try:
        provision = service.provision(run_id, configuration, with_dummy)
        provisioned = True
        (out_dir / "provision.json").write_text(json.dumps(provision, indent=2))
        state = service.load_state(run_id)
        for index, label in enumerate(schedule):
            t0 = time.monotonic()
            sender, receiver = ("alice", "bob") if index % 2 == 0 else ("bob", "alice")
            trial = Trial(run_id, index, state)
            metadata_snapshot(trial, receiver)
            recipe = choose_recipe(state, rng)
            sender_result = {"recipe": None, "target": None, "requests": 0}
            sender_started = time.monotonic()
            if label and scenario != "aa":
                sender_result = sender_recipe(trial, sender, recipe, rng, scenario == "sham")
            remaining = WINDOW - (time.monotonic() - sender_started)
            if remaining > 0:
                time.sleep(remaining)
            time.sleep(SETTLE)
            post_battery(trial, receiver)
            load_extra = max(0, time.monotonic() - t0 - WINDOW - SETTLE)
            gap = GAP + (GAP if sender_result["recipe"] == "load" else 0)
            time.sleep(gap)
            trial_rows.append({
                "trial_id": trial.trial_id, "label": label, "sender": sender, "receiver": receiver,
                "sender_recipe": sender_result["recipe"] if label else None,
                "sender_target": sender_result["target"] if label else None,
                "sender_requests": sender_result["requests"], "action_count": len(trial.rows),
                "elapsed_ms": (time.monotonic() - t0) * 1000, "post_battery_overhead_s": load_extra,
                "inter_trial_gap_s": gap,
            })
            print(json.dumps({"scenario": scenario, **trial_rows[-1]}), flush=True)
        (out_dir / "trials.json").write_text(json.dumps(trial_rows, indent=2))
        server = service.collect(run_id)
        (out_dir / "server.json").write_text(json.dumps(server, indent=2))
        join_counts = write_joined(out_dir, server)
        (out_dir / "scenario.json").write_text(json.dumps({
            "scenario": scenario, "configuration": configuration, "trials": trials,
            "started_utc_ns": started, "finished_utc_ns": time.time_ns(),
            "labels": schedule, "join_counts": join_counts,
        }, indent=2))
    finally:
        if provisioned:
            cleanup = service.cleanup(run_id)
            (out_dir / "cleanup.json").write_text(json.dumps(cleanup, indent=2))
            if not cleanup["absence_proved"]:
                raise RuntimeError(f"cleanup did not prove absence for {scenario}")
    return trial_rows


def final_readback():
    containers = service.run_ok(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    networks = service.run_ok(["docker", "network", "ls", "--format", "{{.Name}}"]).splitlines()
    attached = json.loads(service.run_ok(["docker", "inspect", "art-a", "--format", "{{json .NetworkSettings.Networks}}"]))
    return {
        "p1_containers": [name for name in containers if name.startswith("p1-")],
        "p1_networks": [name for name in networks if name.startswith("p1-")],
        "art_a_p1_networks": [name for name in attached if name.startswith("p1-")],
    }


def write_smoke(results):
    means = {name: sum(row["elapsed_ms"] for row in rows) / len(rows) for name, rows in results.items()}
    full_seconds = sum(means.values()) * 1300 / 1000
    lines = [
        "# P1 smoke run",
        "",
        "This run used 20 balanced, shuffled trials for each scenario: open, combined, separate, A/A, and sham.",
        "It ran on the labhost in the `p1-smoke` tmux session. Requests ran from the isolated client containers.",
        "Raw client records, gateway records, Artifactory records, and marker joins are under `data/smoke/`.",
        "",
        "## Per-trial timing",
        "",
        "| Scenario | Mean elapsed ms |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {name} | {mean:.1f} |" for name, mean in means.items())
    lines.extend([
        "",
        f"Projected sequential wall-clock time for the full 6,500-trial protocol is about {full_seconds / 3600:.1f} hours, based on these scenario means.",
        "This is an operational projection only. The smoke data was not scored, analysed, or interpreted as a channel result.",
        "",
        "## Protocol deviations",
        "",
        "None. Implementation details and planned feature exclusions are recorded in `CHANGES.md`.",
        "",
    ])
    (ROOT / "SMOKE.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args()
    if not args.smoke or args.trials != 20:
        raise SystemExit("This controller only runs the protocol's 20-trial smoke sequence.")
    if DATA.exists():
        raise SystemExit("smoke data already exists; preserve it and use a new reviewed controller if needed")
    DATA.mkdir(parents=True)
    rng = random.Random(SEED)
    scenarios = [("open", "open"), ("combined", "combined"), ("separate", "separate"),
                 ("aa", "open"), ("sham", "open")]
    results = {}
    for scenario, configuration in scenarios:
        results[scenario] = run_scenario(scenario, configuration, args.trials, rng)
    readback = final_readback()
    (DATA / "final-cleanup-readback.json").write_text(json.dumps(readback, indent=2))
    if any(readback.values()):
        raise RuntimeError("final P1 cleanup readback is not empty")
    write_smoke(results)
    print("P1 smoke complete", flush=True)


if __name__ == "__main__":
    main()
