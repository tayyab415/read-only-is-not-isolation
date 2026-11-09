"""Confined subject fixtures. Importing never contacts Docker or AWS."""
import importlib.util
import json
from pathlib import Path
import sys
import time

import actions
import gate

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
sys.path.insert(0, str(P2))
spec = importlib.util.spec_from_file_location("p4_p2_service", P2 / "service.py")
p2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p2)
import harness as p2_harness


def route(public, actor):
    if public.get("kind") == "immutable":
        return {"kind": "immutable", "repository": public["repositories"][actor],
                "object_root": "/fixture", "metadata_root": "/api/storage/snapshot",
                "index_root": "/simple"}
    repo = public["repositories"].get(actor) or public["repositories"]["shared"]
    return {"kind": "mutable", "repository": repo, "object_root": f"/{repo}",
            "metadata_root": f"/api/storage/{repo}-cache", "index_root": f"/{repo}-cache"}


def provision(run_id, configuration, smoke=False):
    if smoke:
        gate.sources()
        p2.check_gate()
    else:
        gate.check()
        p2.check_gate()
    imported = p2.check_sources()
    for filename, key in (("service.py", "p1_service_sha256"),
                          ("gateway.py", "p1_gateway_sha256"),
                          ("dummy.py", "p1_dummy_sha256"),
                          ("upstream.py", "p1_upstream_sha256")):
        if p2.sha(p2.old.ROOT / filename) != imported[key]:
            raise RuntimeError("Original provisioning source differs from its frozen import")
    if configuration not in ("open", "separate", "immutable"):
        raise ValueError("Unapproved subject configuration")
    if configuration == "open":
        public = p2.old.provision(run_id, "open")
        p2.replace_gateway(run_id)
        public.update(kind="mutable", gateway_edition=2, queue_size=5)
        public["boundary_readback"] = p2.boundary_readback(run_id)
        public["network_positive_controls"] = p2.preflight.network_checks(
            p2.old, run_id, HERE / "runtime-config" / f"network-{run_id}.json")
    else:
        public = p2.provision(run_id, configuration)
    if public.get("kind") != "immutable":
        state = p2.old.load_state(run_id)
        public["containers"] = dict(state["containers"])
        public["networks"] = dict(state["networks"])
    # Start logs before any subject call. The serving implementation keeps
    # these private files inside each trusted server.
    if public.get("kind") == "immutable":
        for actor in ("alice", "bob"):
            p2.old.run_ok(["docker", "exec", public["containers"][f"mirror_{actor}"],
                          "touch", "/tmp/base.jsonl", "/tmp/wire.jsonl"])
    return public


class Client:
    def __init__(self, run_id, actor, public):
        self.actor, self.run_id = actor, run_id
        self.route = route(public, actor)
        self.client = p2_harness.Client.sandbox(p2.old, run_id, actor)
        # Baseline's original state names one shared repository. The reused
        # worker's scope checker expects a per-actor route view.
        self.client.state = {**self.client.state,
                             "repositories": {a: route(public, a)["repository"]
                                              for a in ("alice", "bob")}}
        self.turn = 0

    def batch(self, requests):
        checked = [actions.request(row, self.route) for row in requests]
        jobs = [{**row, "probe": f"subject.turn{self.turn:02d}.r{index:02d}",
                 "marker": f"p1/{self.run_id}/a{self.turn:02d}/{self.actor}/r{index:02d}"}
                for index, row in enumerate(checked)]
        self.turn += 1
        if self.turn > actions.MAX_TURNS:
            raise RuntimeError("Subject batch limit exhausted")
        self.client.start(jobs, time.time_ns() + 150_000_000)
        received = self.client.finish()
        if any(row.get("status") is None or row["status"] >= 500 for row in received):
            return {"complete": False, "records": received}
        return {"complete": True, "records": received}

    def close(self):
        self.client.close()


def snapshot(run_id):
    server, lines = p2.collect(run_id)
    return {"server": server, "server_log_lines": lines}


def cleanup(run_id):
    return p2.cleanup(run_id)
