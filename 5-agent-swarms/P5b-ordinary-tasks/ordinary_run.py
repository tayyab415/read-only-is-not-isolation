#!/usr/bin/env python3
"""A diagnostic model and a fixed hidden background schedule."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

import background
import p5_gate
import p5_schedule
from run import Bridge as P4Bridge, SSH
import actions
import prompts
import provider

HERE = Path(__file__).resolve().parent
REMOTE = "cd ~/workspaces/artifactory-lab/next/P5b-subjects && exec python3 -u ordinary_rpc.py"
WALL_S = 240


class Bridge(P4Bridge):
    def __init__(self, spec, smoke=False, command=None):
        self.process = subprocess.Popen(command or [*SSH, REMOTE], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1)
        self.lock, self.pending, self.counter = threading.Lock(), {}, 0
        self.control, self.stopped = queue.Queue(), threading.Event()
        self.thread = threading.Thread(target=self.reader, daemon=True)
        self.thread.start()
        try:
            self.write({"run_id": spec["run_id"], "configuration": spec["configuration"],
                        "scripted_smoke": smoke, "background_active": spec["background_active"]})
            first = self.control.get(timeout=120)
            if not first.get("ready"):
                raise RuntimeError("Ordinary-task fixture did not become ready")
        except Exception:
            self.force_stop()
            raise
        self.public, self.routes = first["public"], first["routes"]

    def batch(self, actor, requests, timing=None):
        if self.stopped.is_set():
            raise RuntimeError("Owned ordinary RPC stopped")
        receiver = queue.Queue()
        with self.lock:
            self.counter += 1
            identifier = self.counter
            self.pending[identifier] = receiver
        job = {"op": "batch", "id": identifier, "actor": actor, "requests": requests}
        if timing is not None:
            job["background_timing"] = timing
        self.write(job)
        result = receiver.get(timeout=45)
        if not result.get("complete"):
            self.stopped.set()
            raise RuntimeError("Confined ordinary request failed")
        return result


def background_loop(bridge, path, phase_epoch_ns):
    records = []
    for index, offset in enumerate(background.WAVE_OFFSETS_S):
        target = phase_epoch_ns + int(offset * 1e9)
        delay = (target - time.monotonic_ns()) / 1e9
        if delay > 0:
            time.sleep(delay)
        dispatch = time.monotonic_ns()
        returned = bridge.batch("bob", background.jobs(bridge.routes["bob"]), timing={
            "wave": index, "planned_offset_s": offset, "phase_epoch_monotonic_ns": phase_epoch_ns,
            "dispatch_monotonic_ns": dispatch})
        record = {"wave": index, "planned_offset_s": offset, "rpc_id": returned["id"],
                  "phase_epoch_monotonic_ns": phase_epoch_ns, "dispatch_monotonic_ns": dispatch,
                  "returned": returned}
        provider.append(path, record)
        records.append(record)
    return {"complete": True, "waves": len(records)}


def ordinary_loop(spec, bridge, folder, ledger, client, phase_epoch_ns):
    delay = (phase_epoch_ns - time.monotonic_ns()) / 1e9
    if delay > 0:
        time.sleep(delay)
    history = [{"role": "user", "content": [{"text": prompts.ordinary(bridge.routes["alice"])}]}]
    started = time.monotonic()

    def scientific_gate():
        p5_gate.check()
        if bridge.stopped.is_set() or time.monotonic() - started >= WALL_S:
            raise RuntimeError("Ordinary infrastructure or time gate closed")

    for turn in range(actions.MAX_TURNS):
        if bridge.stopped.is_set():
            return {"status": "infrastructure_stop"}
        if time.monotonic() - started >= WALL_S:
            return {"status": "time_limit"}
        if turn == actions.MAX_TURNS - 1:
            history[-1]["content"].append({"text": "This is the final turn. Return finish now."})
        returned = provider.invoke(spec["model"], history, folder / "calls.jsonl", scientific_gate,
                                   {"run_id": spec["run_id"], "actor": "alice", "turn": turn,
                                    "study": "ordinary"}, client=client, ledger=ledger)
        if returned["status"] != "completed":
            if returned["status"] == "cost_overrun":
                p5_gate.stop("ordinary_provider_cost_overrun")
            return {key: value for key, value in returned.items() if key != "text"}
        if time.monotonic() - started >= WALL_S:
            return {"status": "time_limit", "last_call_key": returned["call_key"]}
        text = returned["text"]
        history.append({"role": "assistant", "content": [{"text": text or "{}"}]})
        record = {"actor": "alice", "turn": turn, "call_key": returned["call_key"],
                  "returned_text": text, "dispatched": False}
        try:
            action = actions.parse(text, bridge.routes["alice"], study="ordinary")
        except (ValueError, TypeError):
            provider.append(folder / "client-actions.jsonl", {**record, "invalid_action": True})
            history.append({"role": "user", "content": [{"text": '{"error":"Invalid bounded action"}'}]})
            continue
        record["action"] = action
        if action["action"] == "finish":
            provider.append(folder / "client-actions.jsonl", record)
            return {**action, "status": "finished", "claimed_status": action["status"]}
        if turn == actions.MAX_TURNS - 1:
            provider.append(folder / "client-actions.jsonl", record)
            return {"status": "final_turn_did_not_finish"}
        if action["action"] == "wait":
            time.sleep(min(action["seconds"], max(0, WALL_S - (time.monotonic() - started))))
            observation = {"waited_s": action["seconds"]}
        else:
            try:
                response = bridge.batch("alice", action["requests"])
            except Exception:
                p5_gate.stop("ordinary_http_or_rpc_failure")
                provider.append(folder / "client-actions.jsonl", {**record, "infrastructure_failed": True})
                return {"status": "infrastructure_stop"}
            record.update(dispatched=True, rpc_id=response["id"], records=response["records"])
            observation = {"responses": actions.visible(response["records"])}
        record["visible_observation"] = observation
        provider.append(folder / "client-actions.jsonl", record)
        history.append({"role": "user", "content": [{"text": json.dumps(observation)}]})
    return {"status": "action_limit"}


def run_cohort(spec, ledger, client):
    p5_gate.check()
    folder = HERE / "data" / spec["run_id"]
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "preregister.json").write_text(json.dumps(spec, indent=2))
    bridge = None
    status = {"run_id": spec["run_id"], "complete": False,
              "source_freeze_sha256": p5_gate.sha(HERE / "SOURCES-FROZEN.json")}
    try:
        bridge = Bridge(spec)
        (folder / "provision.json").write_text(json.dumps(bridge.public, indent=2))
        phase = time.monotonic_ns() + 200_000_000
        with ThreadPoolExecutor(max_workers=2) as pool:
            shadow = pool.submit(background_loop, bridge, folder / "background.jsonl", phase)
            subject = pool.submit(ordinary_loop, spec, bridge, folder, ledger, client, phase)
            status["outcome"] = subject.result()
            status["background"] = shadow.result()
        status["complete"] = status["background"]["complete"]
    except Exception as error:
        status["failure_type"] = type(error).__name__
        raise
    finally:
        if bridge:
            try:
                status["remote_close"] = bridge.close()
                status["complete"] = status["complete"] and status["remote_close"]["status"]["complete"]
            except Exception as error:
                status.update(complete=False, cleanup_failure_type=type(error).__name__)
                bridge.force_stop()
                p5_gate.stop("ordinary_cleanup_unproved")
        status["budget_summary"] = ledger.summary()
        status["file_sha256"] = {file.name: p5_gate.sha(file) for file in folder.iterdir()
                                 if file.is_file() and file.name != "sealed.json"}
        with (folder / "sealed.json").open("x") as stream:
            json.dump(status, stream, indent=2)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--make-plan", action="store_true")
    args = parser.parse_args()
    path = HERE / "ordinary-plan.json"
    if args.make_plan:
        with path.open("x") as stream:
            json.dump(p5_schedule.ordinary(), stream, indent=2)
        return
    p5_gate.check()
    plan = json.loads(path.read_text())
    if plan != p5_schedule.ordinary():
        raise RuntimeError("Ordinary schedule differs from its frozen assignment")
    ledger, client = provider.budget.Ledger(), provider.make_client()
    for spec in plan:
        print(json.dumps({"launching": spec["run_id"]}), flush=True)
        status = run_cohort(spec, ledger, client)
        print(json.dumps({"run_id": spec["run_id"], "complete": status["complete"],
                          "outcome": status.get("outcome")}), flush=True)
        if not status["complete"] or status["outcome"]["status"] in (
                "budget_stop", "cost_overrun", "infrastructure_stop"):
            break


if __name__ == "__main__":
    main()
