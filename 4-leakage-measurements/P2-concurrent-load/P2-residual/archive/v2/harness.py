"""Persistent sandbox workers with paired, label-independent dispatch."""
import json
from pathlib import Path
import select
import subprocess
import time

import plan
import worker

HERE = Path(__file__).resolve().parent


class Client:
    def __init__(self, command, config, actor, state, target="gateway"):
        self.actor, self.state, self.target = actor, state, target
        self.pending = None
        self.secret = config["password"]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)
        self.proc.stdin.write(json.dumps(config) + "\n")
        self.proc.stdin.flush()
        try:
            ready = self.read(30).get("ready")
        except Exception:
            self.close()
            raise
        if not ready:
            self.close()
            raise RuntimeError("Sandbox worker did not become ready")

    @classmethod
    def sandbox(cls, service, run_id, actor, target="gateway"):
        state = service.load_state(run_id)
        private = service.load_credentials(run_id)
        host = state["containers"].get(f"dummy_{actor}") if target == "dummy" \
            else state["containers"].get(f"mirror_{actor}", state["containers"].get("gateway"))
        if not host:
            raise ValueError("Sandbox target is unavailable")
        command = ["docker", "exec", "-i", state["containers"][actor], "python3", "-u",
                   "-c", (HERE / "worker.py").read_text()]
        config = {"host": host, "port": 8000, "workers": 8,
                  "user": state["users"][actor], "password": private[state["users"][actor]]}
        return cls(command, config, actor, state, target)

    def read(self, timeout):
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            raise RuntimeError(f"{self.actor} worker timed out")
        line = self.proc.stdout.readline()
        if not line:
            detail = self.proc.stderr.read()[-500:].replace(self.secret, "<redacted>")
            raise RuntimeError(f"{self.actor} worker exited: {detail}")
        return json.loads(line)

    def start(self, jobs, epoch_utc_ns, execute_http=1):
        if self.pending is not None:
            raise RuntimeError("A worker RPC is already pending")
        worker.validate_jobs(jobs)
        if not all(plan.scoped(row["path"], self.state, self.actor) for row in jobs):
            raise ValueError("A job leaves its actor's fixture")
        prefix = "" if self.state.get("kind") == "immutable" else "/artifactory"
        wire = [{**row, "path": prefix + row["path"]} for row in jobs]
        if not isinstance(execute_http, int) or isinstance(execute_http, bool) or execute_http not in (0, 1):
            raise ValueError("Invalid private execution flag")
        self.pending = jobs if execute_http else []
        self.proc.stdin.write(json.dumps({"op": "schedule", "jobs": wire,
                                         "epoch_utc_ns": epoch_utc_ns,
                                         "execute_http": execute_http}) + "\n")
        self.proc.stdin.flush()

    def finish(self, timeout=40):
        if self.pending is None:
            raise RuntimeError("No worker RPC is pending")
        jobs = self.pending
        results = self.read(timeout)
        if not isinstance(results, list) or len(results) != len(jobs) \
                or any(result.get("marker") != job["marker"] for job, result in zip(jobs, results)):
            raise RuntimeError("Worker results do not match the scheduled requests")
        self.pending = None
        return [{**result, "path": job["path"], "actor": self.actor, "target": self.target}
                for job, result in zip(jobs, results)]

    def close(self):
        try:
            self.proc.stdin.write('{"op":"exit"}\n')
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)
        finally:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                stream.close()


def run_trial(state, run_id, configuration, trial, clients, sham_clients=None):
    controller_started = time.time_ns()
    sender = (sham_clients or clients)[trial["sender"]] if configuration == "sham" \
        else clients[trial["sender"]]
    receiver = clients[trial["receiver"]]
    send_jobs = plan.sender_jobs(state, run_id, {**trial, "label": 1},
                                "separate" if configuration == "aa" else configuration)
    recv_jobs = plan.receiver_jobs(state, run_id, trial)
    epoch = time.time_ns() + 300_000_000
    # Both RPCs happen on every label, in the same order. The receiver's future
    # schedule includes its post probes and does not wait for sender completion.
    sender.start(send_jobs, epoch, execute_http=int(trial["label"] == 1 and configuration != "aa"))
    receiver.start(recv_jobs, epoch)
    sent, received = sender.finish(), receiver.finish()
    failed = [row["marker"] for row in sent + received
              if row.get("status") is None or row.get("status", 0) >= 500]
    overrun = max((row["end_utc_ns"] for row in sent), default=epoch) - epoch
    return {**trial, "run_id": run_id, "scenario": configuration,
            "epoch_utc_ns": epoch, "sender_records": sent, "pre": [], "post": received,
            "controller_start_utc_ns": controller_started,
            "controller_end_utc_ns": time.time_ns(),
            "scheduled_sender_jobs": len(send_jobs),
            "receiver_schedule_lead_ms": min(
                (epoch - row["schedule_setup_utc_ns"]) / 1e6 for row in received),
            "sender_overrun_ms": max(0, overrun / 1e6 - plan.WINDOW_S * 1000),
            "failed_request_markers": failed, "batch_must_stop": bool(failed)}
