#!/usr/bin/env python3
"""P1 trial runner. Runs on the labhost.

    python3 meter.py smoke            # 20 trials per run, all five runs
    python3 meter.py main RUN         # 300 calibration + 1,000 holdout trials for one run
    python3 meter.py main RUN --resume

Runs: open, combined, separate, aa (open, sender never acts), sham (open,
sender acts against a dummy server on its own isolated network).

Each trial carries one bit. Label 1: the sender executes one recipe drawn from
a fixed grammar during a 1.0 s window. Label 0: it stays idle. The receiver
takes a metadata pre-snapshot before the window and a fixed post-battery after
it. The runner does not analyse anything. Holdout trials go to a separate file
whose hash is sealed when the run ends.
"""
import hashlib
import json
import random
import secrets
import sys
import time
from pathlib import Path

import harness
import service

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = {"open": ("open", False), "combined": ("combined", False), "separate": ("separate", False),
        "aa": ("open", False), "sham": ("open", True)}
RECIPES = ["get", "head", "cond_get", "range_get", "stats", "properties",
           "folder_html", "folder_api", "rdv_get", "rdv_head", "fresh_get", "load"]
WINDOW, SETTLE, GAP, LOAD_EXTRA_GAP, LOAD_CONCURRENCY = 1.0, 0.3, 0.5, 1.0, 8
SENDER_ARTIFACTS = range(15)      # pkg-00..14
TIMING_ARTIFACT = 15              # the receiver's timed GETs; never a sender target
BLOCK = 100                       # server logs are collected every BLOCK trials
SEED = 2026110803


def schedule(run, n_cal, n_hold):
    """Fixed per-run schedule: balanced shuffled labels, a recipe and artifact for every trial."""
    rng = random.Random(f"{SEED}-{run}")
    rows = []
    for split, n in (("calibration", n_cal), ("holdout", n_hold)):
        labels = [0] * (n // 2) + [1] * (n // 2)
        rng.shuffle(labels)
        for label in labels:
            rows.append({"split": split, "label": label, "recipe": rng.choice(RECIPES),
                         "value": rng.choice(SENDER_ARTIFACTS), "token": f"{rng.getrandbits(48):012x}"})
    for index, row in enumerate(rows):
        row["index"] = index
        row["trial_id"] = f"t{index:04d}"
        row["sender"], row["receiver"] = ("alice", "bob") if index % 2 == 0 else ("bob", "alice")
    return rows


def repo_of(state, actor):
    return state["repositories"].get(actor) or state["repositories"]["shared"]


def marker(run_id, trial_id, actor, tag):
    return f"p1/{run_id}/{trial_id}/{actor}/{tag}"


def rendezvous(state, actor, run_id, trial_id, which):
    return f"/{repo_of(state, actor)}/rdv/{run_id}-{trial_id}-{which}.bin"


def pre_battery(state, run_id, trial):
    actor, repo = trial["receiver"], repo_of(state, trial["receiver"])
    return [{"method": "GET", "path": f"/api/storage/{repo}-cache/pkg-{v:02d}.bin?stats",
             "probe": f"pre.stats.{v:02d}", "marker": marker(run_id, trial["trial_id"], actor, f"pre-stats-{v:02d}")}
            for v in range(16)]


def post_battery(state, run_id, trial):
    """Fixed probe order. Non-mutating probes first, then first touches, then the receiver's own downloads."""
    actor, repo, tid = trial["receiver"], repo_of(state, trial["receiver"]), trial["trial_id"]
    probes = []

    def add(name, method, path, headers=None):
        probes.append({"method": method, "path": path, "headers": headers, "probe": name,
                       "marker": marker(run_id, tid, actor, name)})

    for v in range(16):
        add(f"post.stats.{v:02d}", "GET", f"/api/storage/{repo}-cache/pkg-{v:02d}.bin?stats")
    for v in range(16):
        add(f"post.properties.{v:02d}", "GET", f"/api/storage/{repo}-cache/pkg-{v:02d}.bin?properties")
    for v in range(16):
        add(f"post.head.{v:02d}", "HEAD", f"/{repo}/pkg-{v:02d}.bin")
    add("post.folder_html", "GET", f"/{repo}-cache/")
    add("post.folder_api", "GET", f"/api/storage/{repo}-cache/")
    add("post.rdv_a", "GET", rendezvous(state, actor, run_id, tid, "a"))
    add("post.rdv_c", "GET", rendezvous(state, actor, run_id, tid, "c"))
    add("post.fresh", "GET", f"/{repo}/fresh/{run_id}-{tid}-r-{trial['token']}.bin")
    for i in range(5):
        add(f"post.timed.{i}", "GET", f"/{repo}/pkg-{TIMING_ARTIFACT:02d}.bin")
    return probes


def sender_requests(state, run_id, trial, burst_index=0):
    """The single request a recipe makes (or one burst of the load recipe)."""
    actor, repo, tid, v = trial["sender"], repo_of(state, trial["sender"]), trial["trial_id"], trial["value"]
    art, store = f"/{repo}/pkg-{v:02d}.bin", f"/api/storage/{repo}-cache/pkg-{v:02d}.bin"
    recipe = trial["recipe"]
    spec = {
        "get": ("GET", art, None),
        "head": ("HEAD", art, None),
        "cond_get": ("GET", art, {"If-None-Match": state["fixture_etag"]}),
        "range_get": ("GET", art, {"Range": "bytes=0-0"}),
        "stats": ("GET", store + "?stats", None),
        "properties": ("GET", store + "?properties", None),
        "folder_html": ("GET", f"/{repo}-cache/", None),
        "folder_api": ("GET", f"/api/storage/{repo}-cache/", None),
        "rdv_get": ("GET", rendezvous(state, actor, run_id, tid, "a"), None),
        "rdv_head": ("HEAD", rendezvous(state, actor, run_id, tid, "a"), None),
        "fresh_get": ("GET", f"/{repo}/fresh/{run_id}-{tid}-s-{trial['token']}.bin", None),
        "load": ("GET", art, None),
    }[recipe]
    count = LOAD_CONCURRENCY if recipe == "load" else 1
    return [{"method": spec[0], "path": spec[1], "headers": spec[2], "probe": f"sender.{recipe}",
             "marker": marker(run_id, tid, actor, f"s-{recipe}-{burst_index}-{i}")} for i in range(count)]


def run_trial(state, run_id, scenario, trial, clients):
    sender, receiver = clients[trial["sender"]], clients[trial["receiver"]]
    t0 = time.monotonic()
    pre = receiver.seq(pre_battery(state, run_id, trial))
    window_start = time.monotonic()
    sent = []
    if trial["label"] == 1 and scenario != "aa":
        target = "dummy" if scenario == "sham" else "gateway"
        if trial["recipe"] == "load":
            burst = 0
            while time.monotonic() - window_start < WINDOW:
                reqs = sender_requests(state, run_id, trial, burst)
                if target == "dummy":
                    reqs = [{**r, "target": "dummy", "path": "/sham" + r["path"]} for r in reqs]
                sent += sender.par(reqs)
                burst += 1
        else:
            reqs = sender_requests(state, run_id, trial)
            if target == "dummy":
                reqs = [{**r, "target": "dummy", "path": "/sham" + r["path"]} for r in reqs]
            sent += sender.seq(reqs)
    window_used = time.monotonic() - window_start
    if window_used < WINDOW:
        time.sleep(WINDOW - window_used)
    time.sleep(SETTLE)
    post = receiver.seq(post_battery(state, run_id, trial))
    elapsed = time.monotonic() - t0
    time.sleep(GAP + (LOAD_EXTRA_GAP if trial["recipe"] == "load" and trial["label"] == 1 else 0))
    return {**trial, "run_id": run_id, "scenario": scenario, "window_used_s": window_used,
            "trial_elapsed_s": elapsed, "sender_records": sent, "pre": pre, "post": post}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(phase, scenario, n_cal, n_hold, resume=False):
    configuration, with_dummy = RUNS[scenario]
    run_id = f"{phase}-{scenario}"
    out = DATA / phase / scenario
    if out.exists() and not resume:
        raise SystemExit(f"{out} exists; use --resume or a new phase id")
    out.mkdir(parents=True, exist_ok=True)
    rows = schedule(scenario, n_cal, n_hold)
    (out / "schedule.json").write_text(json.dumps(rows))
    files = {"calibration": out / "calibration.jsonl", "holdout": out / "holdout.jsonl"}
    done = sum(1 for f in files.values() if f.exists() for _ in f.open())
    if not service.state_path(run_id).exists():
        if done:
            raise SystemExit("fixtures are gone; cannot resume a partially written run")
        provision = service.provision(run_id, configuration, with_dummy)
        (out / "provision.json").write_text(json.dumps(provision, indent=2))
    state = service.load_state(run_id)
    clients = {actor: harness.Client(run_id, actor) for actor in ("alice", "bob")}
    log_lines = set()
    log_path = out / "server-log-lines.txt"
    if log_path.exists():
        log_lines.update(log_path.read_text().splitlines())
    try:
        for trial in rows[done:]:
            record = run_trial(state, run_id, scenario, trial, clients)
            with files[trial["split"]].open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            print(json.dumps({"run": run_id, "trial": trial["trial_id"], "split": trial["split"],
                              "label": trial["label"], "recipe": trial["recipe"],
                              "elapsed_s": round(record["trial_elapsed_s"], 2)}), flush=True)
            if (trial["index"] + 1) % BLOCK == 0:
                log_lines.update(harness.server_log_lines(run_id))
                log_path.write_text("\n".join(sorted(log_lines)) + "\n")
    finally:
        for client in clients.values():
            client.close()
    log_lines.update(harness.server_log_lines(run_id))
    log_path.write_text("\n".join(sorted(log_lines)) + "\n")
    server = service.collect(run_id)
    server.pop("artifactory_request_log_lines", None)   # superseded by server-log-lines.txt
    server.pop("artifactory_router_log", None)
    (out / "server.json").write_text(json.dumps(server))
    sealed = {name: {"sha256": digest(path), "trials": sum(1 for _ in path.open())}
              for name, path in files.items() if path.exists()}
    (out / "sealed.json").write_text(json.dumps({"run_id": run_id, "files": sealed,
                                                 "sealed_utc_ns": time.time_ns()}, indent=2))
    cleanup = service.cleanup(run_id)
    (out / "cleanup.json").write_text(json.dumps(cleanup, indent=2))
    if not cleanup["absence_proved"]:
        raise RuntimeError("cleanup did not prove absence")
    print(json.dumps({"run": run_id, "complete": True, "sealed": sealed}), flush=True)


def main():
    if sys.argv[1] == "smoke":
        for scenario in RUNS:
            execute("s1", scenario, 10, 10)
    elif sys.argv[1] == "main":
        execute("m1", sys.argv[2], 300, 1000, resume="--resume" in sys.argv)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
