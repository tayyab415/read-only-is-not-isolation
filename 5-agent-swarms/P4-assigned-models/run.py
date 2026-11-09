#!/usr/bin/env python3
"""Mac-side model loops. All HTTP actions execute in the owned lab host fixture."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

import actions
import gate
import prompts
import provider
import schedule

HERE = Path(__file__).resolve().parent
SSH = ["ssh", "-o", "ControlMaster=auto", "-o", "ControlPersist=10m",
       "-o", "ControlPath=/tmp/p1-research-cm-%r@%h:%p", "-o", "BatchMode=yes",
       "-o", "ConnectTimeout=10", os.environ.get("P4_SSH_HOST", "lab-host")]
REMOTE = "cd ~/workspaces/artifactory-lab/next/P4-models && exec python3 -u rpc.py"
WALL_S = 240


class Bridge:
    """Multiplex independent client requests without peer completion barriers."""
    def __init__(self, spec, smoke=False, command=None):
        self.process = subprocess.Popen(command or [*SSH, REMOTE], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1)
        self.lock, self.pending, self.counter = threading.Lock(), {}, 0
        self.control = queue.Queue()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.reader, daemon=True)
        self.thread.start()
        try:
            self.write({"run_id": spec["run_id"], "configuration": spec["configuration"],
                        "scripted_smoke": smoke})
            first = self.control.get(timeout=120)
            if not first.get("ready"):
                raise RuntimeError("The owned subject fixture did not become ready")
        except Exception:
            self.force_stop()
            raise
        self.public, self.routes = first["public"], first["routes"]

    def write(self, value):
        with self.lock:
            self.process.stdin.write(json.dumps(value) + "\n")
            self.process.stdin.flush()

    def reader(self):
        try:
            for line in self.process.stdout:
                result = json.loads(line)
                if "id" in result:
                    with self.lock:
                        receiver = self.pending.pop(result["id"], None)
                    if receiver is None:
                        raise RuntimeError("Unexpected RPC response")
                    if not result.get("complete"):
                        self.stopped.set()
                    receiver.put(result)
                else:
                    self.control.put(result)
        except Exception:
            self.stopped.set()
        finally:
            self.stopped.set()
            self.control.put({"rpc_stream_closed": True})
            with self.lock:
                for receiver in self.pending.values():
                    receiver.put({"complete": False, "rpc_stream_closed": True})
                self.pending.clear()

    def batch(self, actor, requests):
        if self.stopped.is_set():
            raise RuntimeError("Owned RPC is stopped")
        receiver = queue.Queue()
        with self.lock:
            self.counter += 1
            identifier = self.counter
            self.pending[identifier] = receiver
        self.write({"op": "batch", "id": identifier, "actor": actor, "requests": requests})
        result = receiver.get(timeout=45)
        if not result.get("complete"):
            self.stopped.set()
            raise RuntimeError("A confined request batch failed")
        return result

    def close(self):
        self.write({"op": "close"})
        result = self.control.get(timeout=90)
        self.process.stdin.close()
        self.process.wait(timeout=15)
        self.thread.join(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()
        if not result.get("closed") or self.process.returncode \
                or not result["status"].get("cleanup_absence_proved"):
            raise RuntimeError("Subject fixture cleanup is incomplete")
        return result

    def force_stop(self):
        try:
            self.process.stdin.close()
        except Exception:
            pass
        try:
            self.process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.thread.join(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()
        self.stopped.set()


def actor_loop(spec, actor, bridge, folder, ledger, client):
    peer = "bob" if actor == "alice" else "alice"
    prompt = prompts.assigned(actor, peer, spec["values"][actor], bridge.routes[actor],
                              bridge.public["users"][peer], spec["public_token"])
    history = [{"role": "user", "content": [{"text": prompt}]}]
    start = time.monotonic()
    outcome = {"status": "action_limit"}

    def scientific_gate():
        gate.check()
        if bridge.stopped.is_set() or time.monotonic() - start >= WALL_S:
            raise RuntimeError("Cohort infrastructure or time gate closed")

    for turn in range(actions.MAX_TURNS):
        if bridge.stopped.is_set():
            return {"status": "infrastructure_stop"}
        if time.monotonic() - start >= WALL_S:
            return {"status": "time_limit"}
        if turn == actions.MAX_TURNS - 1:
            history[-1]["content"].append({"text": "This is the final turn. Return finish now."})
        result = provider.invoke(spec["model"], history, folder / "calls.jsonl",
                                 scientific_gate, {"run_id": spec["run_id"], "actor": actor,
                                                   "turn": turn, "study": spec["study"]},
                                 client=client, ledger=ledger)
        if result["status"] != "completed":
            if result["status"] == "cost_overrun":
                gate.stop("provider_cost_overrun")
            return {key: value for key, value in result.items() if key != "text"}
        if time.monotonic() - start >= WALL_S:
            return {"status": "time_limit", "last_call_key": result["call_key"]}
        text = result["text"]
        history.append({"role": "assistant", "content": [{"text": text or "{}"}]})
        record = {"actor": actor, "turn": turn, "call_key": result["call_key"],
                  "returned_text": text, "dispatched": False}
        try:
            action = actions.parse(text, bridge.routes[actor])
        except (ValueError, TypeError):
            record["invalid_action"] = True
            provider.append(folder / "client-actions.jsonl", record)
            history.append({"role": "user", "content": [{"text": '{"error":"Invalid bounded action"}'}]})
            continue
        record["action"] = action
        if action["action"] == "finish":
            provider.append(folder / "client-actions.jsonl", record)
            outcome = {**action, "status": "finished", "claimed_status": action["status"]}
            break
        if turn == actions.MAX_TURNS - 1:
            provider.append(folder / "client-actions.jsonl", record)
            return {"status": "final_turn_did_not_finish"}
        if action["action"] == "wait":
            time.sleep(min(action["seconds"], max(0, WALL_S - (time.monotonic() - start))))
            observation = {"waited_s": action["seconds"]}
        else:
            try:
                returned = bridge.batch(actor, action["requests"])
            except Exception:
                gate.stop("subject_http_or_rpc_failure")
                provider.append(folder / "client-actions.jsonl", {**record, "infrastructure_failed": True})
                return {"status": "infrastructure_stop"}
            record.update(dispatched=True, rpc_id=returned["id"], records=returned["records"])
            observation = {"responses": actions.visible(returned["records"])}
        record["visible_observation"] = observation
        provider.append(folder / "client-actions.jsonl", record)
        history.append({"role": "user", "content": [{"text": json.dumps(observation)}]})
    return outcome


def run_cohort(spec, ledger, client):
    gate.check()
    folder = HERE / "data" / spec["run_id"]
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "preregister.json").write_text(json.dumps(spec, indent=2))
    bridge = None
    status = {"complete": False, "run_id": spec["run_id"],
              "source_freeze_sha256": gate.sha(HERE / "SOURCES-FROZEN.json")}
    try:
        bridge = Bridge(spec)
        (folder / "provision.json").write_text(json.dumps(bridge.public, indent=2))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {actor: pool.submit(actor_loop, spec, actor, bridge, folder, ledger, client)
                       for actor in ("alice", "bob")}
            status["outcomes"] = {actor: future.result() for actor, future in futures.items()}
        status["complete"] = True
    except Exception as error:
        status["failure_type"] = type(error).__name__
        raise
    finally:
        if bridge is not None:
            try:
                status["remote_close"] = bridge.close()
                status["complete"] = status["complete"] \
                    and status["remote_close"]["status"]["complete"]
            except Exception as error:
                status.update(complete=False, cleanup_failure_type=type(error).__name__)
                bridge.force_stop()
                if not (HERE / "STOP.json").exists():
                    gate.stop("subject_cleanup_unproved")
        status["budget_summary"] = ledger.summary()
        status["file_sha256"] = {file.name: gate.sha(file) for file in folder.iterdir()
                                 if file.is_file() and file.name != "sealed.json"}
        with (folder / "sealed.json").open("x") as stream:
            json.dump(status, stream, indent=2)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--make-plan", action="store_true")
    args = parser.parse_args()
    plan_path = HERE / "assigned-plan.json"
    if args.make_plan:
        with plan_path.open("x") as stream:
            json.dump(schedule.assigned(), stream, indent=2)
        return
    gate.check()
    plan = json.loads(plan_path.read_text())
    if plan != schedule.assigned():
        raise RuntimeError("The model schedule differs from its prospective assignment")
    ledger = provider.budget.Ledger()
    client = provider.make_client()
    for spec in plan:
        if ledger.summary()["must_stop"] or (HERE / "STOP.json").exists():
            break
        print(json.dumps({"launching": spec["run_id"]}), flush=True)
        result = run_cohort(spec, ledger, client)
        print(json.dumps({"run_id": spec["run_id"], "complete": result["complete"],
                          "outcomes": result.get("outcomes")}), flush=True)
        if not result["complete"] or any(row.get("status") == "budget_stop"
                for row in result.get("outcomes", {}).values()):
            break


if __name__ == "__main__":
    main()
