"""Owned, P1-gated Linux utility deployment. Importing it has no I/O to a VM."""
import argparse
import base64
import fcntl
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import random
import select
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
P3 = HERE.parent / "P3-mirror"
SERVING = HERE.parent / "P3-isolation"
sys.path.insert(0, str(P2))
spec = importlib.util.spec_from_file_location("p2_utility_service", P2 / "service.py")
p2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p2)
old = p2.old
SEED = 2026110809


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule():
    requirements = [line for line in (P3 / "requirements.txt").read_text().splitlines() if line.strip()]
    tasks = [{"actor": actor, "requirement": requirement} for actor in ("alice", "bob")
             for requirement in requirements]
    random.Random(SEED).shuffle(tasks)
    for index, row in enumerate(tasks):
        row["task_id"] = f"u{index:02d}-{row['actor']}-{row['requirement'].split('==')[0]}"
    return tasks


def arm_order():
    arms = ["separate", "immutable"]
    random.Random(SEED).shuffle(arms)
    return arms


def check_freeze():
    freeze = json.loads((HERE / "UTILITY-FROZEN.json").read_text())
    for name, expected in freeze["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError("Frozen Linux utility source changed")
    for name, expected in freeze["import_sha256"].items():
        if sha(HERE.parent / name) != expected:
            raise RuntimeError("Frozen Linux utility import changed")
    if freeze["tasks"] != schedule() or freeze["arms"] != arm_order():
        raise RuntimeError("Linux utility schedule changed")
    p2.check_sources()
    # The reused provisioner mounts its original runtime directory.
    for name in ("service.py", "gateway.py", "dummy.py", "upstream.py"):
        if sha(old.ROOT / name) != freeze["import_sha256"][f"P1-meter/{name}"]:
            raise RuntimeError("Original lab-host-side provisioning source changed")
    return freeze


class ServerProcess:
    def __init__(self, container, config):
        self.proc = subprocess.Popen(
            ["docker", "exec", "-i", container, "python3", "-u", "-c", (HERE / "adapter.py").read_text(),
             "--source", "/mirror.py", "--manifest", "/catalog/manifest.json", "--log", "/tmp/adapter.jsonl"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.proc.stdin.write(json.dumps(config) + "\n")
        self.proc.stdin.flush()
        try:
            if not select.select([self.proc.stdout], [], [], 15)[0]:
                raise RuntimeError("Utility adapter readiness timed out")
            self.ready = json.loads(self.proc.stdout.readline())
            if not self.ready.get("ready"):
                raise RuntimeError("Utility adapter failed readiness")
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            self.proc.stdin.write("stop\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
            if self.proc.returncode:
                raise RuntimeError("Utility adapter did not stop cleanly")
        except Exception:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=5)
            raise
        finally:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                stream.close()


def serving_container(name, network, config_path):
    p2.mirror_sandbox(name, network,
        ("-v", f"{P3 / 'mirror.py'}:/mirror.py:ro",
         "-v", f"{SERVING / 'deploy.py'}:/deploy.py:ro",
         "-v", f"{P3 / 'data'}:/catalog:ro", "-v", f"{config_path}:/config.json:ro"),
        ("python3", "-c",
         "from pathlib import Path; import os; "
         "Path('/tmp/base.jsonl').touch(exist_ok=False); "
         "Path('/tmp/wire.jsonl').touch(exist_ok=False); "
         "os.execvp('python3',['python3',*__import__('sys').argv[1:]])",
         "/deploy.py", "--mirror-source", "/mirror.py",
         "--manifest", "/catalog/manifest.json", "--config", "/config.json",
         "--log", "/tmp/base.jsonl", "--wire-log", "/tmp/wire.jsonl"))


def replace_upstream(run_id):
    state = old.load_state(run_id)
    name, network = state["containers"]["upstream"], state["networks"]["services"]
    original = old.docker_log(name)
    config = {"tenant": "trusted-admission", "queue_size": 5,
              "origin_binding": {old.ip_address("art-a", network): "trusted-admission"},
              "mirror_source_sha256": sha(P3 / "mirror.py"), "manifest_sha256": sha(P3 / "data/manifest.json")}
    path = HERE / "runtime-config" / f"upstream-{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    old.run_ok(["docker", "rm", "-f", name])
    serving_container(name, network, path)
    old.wait_ready(state["containers"]["gateway"], name)
    return {"bootstrap_upstream_log": original, "artifactory_origin_ip": next(iter(config["origin_binding"])),
            "service_network": network, "origin_config_sha256": sha(path)}


def prewarm(run_id, manifest, records=None):
    state, private = old.load_state(run_id), old.load_credentials(run_id)
    user = state["users"]["prefetch"]
    authorization = "Basic " + base64.b64encode((user + ":" + private[user]).encode()).decode()
    records = [] if records is None else records
    for actor in ("alice", "bob"):
        for entry in manifest["entries"]:
            route = f"/files/{entry['sha256']}/{entry['filename']}"
            path = "/artifactory/" + state["repositories"][actor] + route
            marker = f"p1/{run_id}/utility/admission/{actor}/{entry['sha256'][:12]}"
            connection = http.client.HTTPConnection("127.0.0.1", 18584, timeout=30)
            started = time.time_ns()
            try:
                connection.request("GET", path, headers={"Authorization": authorization, "User-Agent": marker})
                response = connection.getresponse()
                body = response.read(entry["bytes"] + 1)
                record = {"actor": actor, "method": "GET", "path": path, "marker": marker,
                          "principal": user, "status": response.status,
                          "start_utc_ns": started, "end_utc_ns": time.time_ns(),
                          "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
                          "expected_sha256": entry["sha256"], "expected_bytes": entry["bytes"]}
                records.append(record)
                if response.status != 200 or len(body) != entry["bytes"] \
                        or record["body_sha256"] != entry["sha256"]:
                    raise RuntimeError("Trusted utility admission failed")
            finally:
                connection.close()
    return records


def utility_clients(run_id):
    state = old.load_state(run_id)
    for actor in ("alice", "bob"):
        name = state["containers"][actor]
        old.run_ok(["docker", "rm", "-f", name])
        old.run_ok(["docker", "run", "-d", "--name", name, "--network", state["networks"][actor],
                    "--user", "20001:20001", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true", "--memory", "256m",
                    "--memory-swap", "256m", "--cpus", "0.5", "--pids-limit", "48",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m", "python:3.12-slim",
                    "python3", "-c", "import time; time.sleep(86400)"])


def boundary_readback(run_id):
    state = old.load_state(run_id)
    immutable = state.get("kind") == "immutable"
    expected_roles = {"alice", "bob", "adapter_alice", "adapter_bob"} | (
        {"mirror_alice", "mirror_bob"} if immutable else {"gateway", "upstream"})
    expected_networks = {"alice", "bob"} if immutable else {"alice", "bob", "services"}
    if set(state["containers"]) != expected_roles or set(state["networks"]) != expected_networks:
        raise RuntimeError("Utility runtime inventory differs")
    output = {"containers": {}, "networks": {}}
    for role, name in state["containers"].items():
        item = json.loads(old.run_ok(["docker", "inspect", name]))[0]
        host, config = item["HostConfig"], item["Config"]
        forbidden = any(entry.split("=", 1)[0].startswith(("AWS_", "BEDROCK_")) for entry in config.get("Env", []))
        memory = 256 * 1024 * 1024 if role in ("alice", "bob") else 128 * 1024 * 1024
        if role == "gateway":
            attached = {state["networks"][key] for key in ("alice", "bob", "services")}
        elif role == "upstream":
            attached = {state["networks"]["services"]}
        else:
            attached = {state["networks"][role.split("_")[-1]]}
        row = {"name": name, "user": config["User"], "root_read_only": host["ReadonlyRootfs"],
               "cap_drop": host["CapDrop"], "security_options": host["SecurityOpt"],
               "memory": host["Memory"], "memory_swap": host["MemorySwap"],
               "nano_cpus": host["NanoCpus"], "pids_limit": host["PidsLimit"], "tmpfs": host["Tmpfs"],
               "forbidden_environment_present": forbidden, "image_id": item["Image"],
               "published_ports": item["NetworkSettings"]["Ports"],
               "network_addresses": {key: value["IPAddress"] for key, value in item["NetworkSettings"]["Networks"].items()},
               "bind_mount_destinations": [mount["Destination"] for mount in item["Mounts"] if mount["Type"] == "bind"],
               "mounts_read_only": all(not mount["RW"] for mount in item["Mounts"] if mount["Type"] == "bind")}
        if row["user"] != "20001:20001" or not row["root_read_only"] or forbidden \
                or row["memory"] != memory or row["memory_swap"] != memory \
                or row["nano_cpus"] != 500000000 or row["pids_limit"] != 48 \
                or "ALL" not in (row["cap_drop"] or []) \
                or "no-new-privileges:true" not in (row["security_options"] or []) \
                or any(row["published_ports"].values()) or not row["mounts_read_only"] \
                or set(row["network_addresses"]) != attached or not all(row["network_addresses"].values()) \
                or role in ("alice", "bob") and (row["bind_mount_destinations"]
                    or "size=128m" not in row["tmpfs"].get("/tmp", "")):
            raise RuntimeError("Utility sandbox differs from its declared boundary")
        output["containers"][role] = row
    for role, name in state["networks"].items():
        item = json.loads(old.run_ok(["docker", "network", "inspect", name]))[0]
        row = {"name": name, "internal": item["Internal"], "options": item["Options"]}
        if not row["internal"] or row["options"].get("com.docker.network.bridge.gateway_mode_ipv4") != "isolated":
            raise RuntimeError("Utility network differs from its declared boundary")
        output["networks"][role] = row
    return output


def start_adapters(run_id, mode, tasks, processes):
    state = old.load_state(run_id)
    state["containers"].update({f"adapter_{actor}": f"p1-{run_id}-adapter-{actor}" for actor in ("alice", "bob")})
    old.state_path(run_id).write_text(json.dumps(state))
    for actor in ("alice", "bob"):
        old.sandbox(state["containers"][f"adapter_{actor}"], state["networks"][actor],
                    extra=("-v", f"{P3 / 'mirror.py'}:/mirror.py:ro",
                           "-v", f"{P3 / 'data'}:/catalog:ro"))
    adapter_ips = {actor: old.ip_address(state["containers"][f"adapter_{actor}"], state["networks"][actor])
                   for actor in ("alice", "bob")}
    if mode == "separate":
        config_path = old.ROOT / f"gateway-{run_id}.json"
        config = json.loads(config_path.read_text())
        config["origin_binding"] = {adapter_ips[actor]: state["users"][actor] for actor in ("alice", "bob")}
        config_path.write_text(json.dumps(config))
        p2.replace_gateway(run_id)
    else:
        for actor in ("alice", "bob"):
            path = P2 / "runtime-config" / f"mirror-{run_id}-{actor}.json"
            config = json.loads(path.read_text())
            config["origin_binding"] = {adapter_ips[actor]: state["users"][actor]}
            path.write_text(json.dumps(config))
            name = state["containers"][f"mirror_{actor}"]
            old.run_ok(["docker", "rm", "-f", name])
            serving_container(name, state["networks"][actor], path)
            old.wait_ready(state["containers"][actor], name)
    private = old.load_credentials(run_id)
    readiness = {}
    for actor in ("alice", "bob"):
        config = {"mode": "mutable" if mode == "separate" else "immutable",
                  "actor": actor, "run_id": run_id,
                  "origin_binding": {old.ip_address(state["containers"][actor], state["networks"][actor]): state["users"][actor]},
                  "tasks": [row["task_id"] for row in tasks if row["actor"] == actor],
                  "backend_host": state["containers"]["gateway"] if mode == "separate" else state["containers"][f"mirror_{actor}"],
                  "backend_port": 8000,
                  "backend_prefix": "/artifactory/" + state["repositories"][actor] if mode == "separate" else "",
                  "mirror_source_sha256": sha(P3 / "mirror.py"), "manifest_sha256": sha(P3 / "data/manifest.json")}
        if mode == "separate":
            config.update(user=state["users"][actor], password=private[state["users"][actor]])
        process = ServerProcess(state["containers"][f"adapter_{actor}"], config)
        processes.append(process)
        readiness[actor] = process.ready
    return readiness


def adapter_routes(run_id):
    state = old.load_state(run_id)
    results = []
    for actor in ("alice", "bob"):
        peer = "bob" if actor == "alice" else "alice"
        jobs = [{"name": "assigned index adapter", "host": state["containers"][f"adapter_{actor}"],
                 "port": 8000, "expected_connected": True},
                {"name": "peer index adapter", "host": old.ip_address(
                    state["containers"][f"adapter_{peer}"], state["networks"][peer]),
                 "port": 8000, "expected_connected": False}]
        result = old.run(["docker", "exec", "-i", state["containers"][actor], "python3", "-c", p2.preflight.PROBE],
                         input=json.dumps(jobs), timeout=8)
        if result.returncode:
            raise RuntimeError("Utility route probe failed")
        rows = json.loads(result.stdout)
        if len(rows) != len(jobs) or any(any(row[key] != job[key] for key in job)
                                      for row, job in zip(rows, jobs)):
            raise RuntimeError("Utility route result differs from its target")
        results.extend({**row, "actor": actor} for row in rows)
    return p2.preflight.check_results(results)


HTTP_PROBE = """
import hashlib,http.client,json,sys,time
job=json.loads(sys.stdin.readline())
started=time.time_ns()
connection=http.client.HTTPConnection(job["host"],8000,timeout=3)
try:
    connection.request("GET",job["path"],headers={"User-Agent":job["marker"]})
    response=connection.getresponse(); body=response.read()
    print(json.dumps({**job,"method":"GET","status":response.status,
        "body_sha256":hashlib.sha256(body).hexdigest(),"body_bytes":len(body),
        "start_utc_ns":started,"end_utc_ns":time.time_ns()}),flush=True)
finally:
    connection.close()
"""


def direct_http_controls(run_id, mode, manifest):
    state = old.load_state(run_id)
    entry = manifest["entries"][0]
    route = f"/files/{entry['sha256']}/{entry['filename']}"
    records = []
    for actor in ("alice", "bob"):
        host = state["containers"]["gateway"] if mode == "separate" else state["containers"][f"mirror_{actor}"]
        path = "/artifactory/" + state["repositories"][actor] + route if mode == "separate" else route
        job = {"actor": actor, "host": host, "path": path,
               "marker": f"p1/{run_id}/utility/control/{actor}", "expected_status": 403}
        result = old.run(["docker", "exec", "-i", state["containers"][actor], "python3", "-c", HTTP_PROBE],
                         input=json.dumps(job), timeout=8)
        if result.returncode:
            raise RuntimeError("Direct utility route control failed")
        row = json.loads(result.stdout)
        if any(row.get(key) != value for key, value in job.items()) or row["status"] != 403:
            raise RuntimeError("A client bypassed its utility adapter")
        records.append(row)
    return {"passed": True, "records": records, "scope": "Uncredentialed direct HTTP requests from both clients."}


def memory_snapshot(run_id):
    state = old.load_state(run_id)
    names = list(state["containers"].values())
    if state.get("kind") != "immutable":
        names += ["art-a", "art-a-db"]
    text = old.run_ok(["docker", "stats", "--no-stream", "--format", "{{json .}}", *names])
    return {"utc_ns": time.time_ns(), "method": "Docker CLI usage snapshots, not peak or process RSS",
            "records": [{key: row.get(key) for key in ("Name", "MemUsage", "CPUPerc", "PIDs", "NetIO", "BlockIO")}
                        for row in (json.loads(line) for line in text.splitlines())]}


def task(run_id, row, manifest):
    state = old.load_state(run_id)
    actor = row["actor"]
    config = {**row, "host": state["containers"][f"adapter_{actor}"], "port": 8000,
              "manifest": manifest, "check_source_text": (P3 / "utility.py").read_text(),
              "check_source_sha256": sha(P3 / "utility.py")}
    result = old.run(["docker", "exec", "-i", state["containers"][actor], "python3", "-u",
                      "-c", (HERE / "client.py").read_text()],
                     input=json.dumps(config), timeout=165)
    if result.returncode:
        raise RuntimeError("Sandbox utility runner failed")
    output = json.loads(result.stdout)
    if any(output.get(key) != value for key, value in row.items()):
        raise RuntimeError("Utility result differs from its scheduled task")
    return output


def collect(run_id, mode):
    state = old.load_state(run_id)
    server, lines = p2.collect(run_id)
    server["adapter_logs"] = {actor: [json.loads(line) for line in old.run_ok(
        ["docker", "exec", state["containers"][f"adapter_{actor}"], "cat", "/tmp/adapter.jsonl"]).splitlines()]
        for actor in ("alice", "bob")}
    if mode == "separate":
        name = state["containers"]["upstream"]
        server["package_upstream_response_log"] = [json.loads(line) for line in old.run_ok(
            ["docker", "exec", name, "cat", "/tmp/base.jsonl"]).splitlines()]
        server["package_upstream_wire_log"] = [json.loads(line) for line in old.run_ok(
            ["docker", "exec", name, "cat", "/tmp/wire.jsonl"]).splitlines()]
    return server, lines


def adapter_failures(run_id):
    state = old.load_state(run_id)
    for actor in ("alice", "bob"):
        text = old.run_ok(["docker", "exec", state["containers"][f"adapter_{actor}"], "cat", "/tmp/adapter.jsonl"])
        if any(row["status"] >= 500 for row in (json.loads(line) for line in text.splitlines())):
            raise RuntimeError("Utility backend integrity or availability failed")


def cleanup(run_id):
    for actor in ("alice", "bob"):
        old.run(["docker", "rm", "-f", f"p1-{run_id}-adapter-{actor}"])
    return p2.cleanup(run_id)


def execute_arm(data, mode):
    freeze = check_freeze()
    gate = p2.check_gate()
    if data.name != "u1" or mode not in freeze["arms"]:
        raise ValueError("Unknown utility dataset")
    run_id = "p3u-m1-" + mode
    folder = data / mode
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "tasks.jsonl"
    if output.exists() or (folder / "sealed.json").exists():
        raise RuntimeError("This utility arm already has retained evidence")
    tasks, manifest = freeze["tasks"], json.loads((P3 / "data/manifest.json").read_text())
    processes, records = [], []
    status = {"run_id": run_id, "mode": mode, "complete": False, "scheduled": 28,
              "source_freeze_sha256": sha(HERE / "UTILITY-FROZEN.json")}
    prepared = {"run_id": run_id, "mode": mode, "schedule": tasks, "p1_gate": gate,
                "p1_gate_sha256": sha(P2 / "ALLOW-COLLECTION.json")}
    try:
        prepared["initial_provision"] = p2.provision(run_id, mode)
        utility_clients(run_id)
        if mode == "separate":
            prepared["package_source_setup"] = replace_upstream(run_id)
            prepared["trusted_package_prewarm"] = []
            prewarm(run_id, manifest, prepared["trusted_package_prewarm"])
        prepared["adapters"] = start_adapters(run_id, mode, tasks, processes)
        prepared["boundary_readback"] = boundary_readback(run_id)
        prepared["network_positive_controls"] = p2.preflight.network_checks(old, run_id)
        prepared["adapter_route_controls"] = adapter_routes(run_id)
        prepared["direct_http_controls"] = direct_http_controls(run_id, mode, manifest)
        prepared["memory_before"] = memory_snapshot(run_id)
        (folder / "prepared.json").write_text(json.dumps(prepared, indent=2))
        with output.open("x") as stream:
            for row in tasks:
                result = task(run_id, row, manifest)
                records.append(result)
                stream.write(json.dumps(result) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                print(json.dumps({"mode": mode, "task_id": row["task_id"], "passed": result["passed"]}), flush=True)
                adapter_failures(run_id)
        status["complete"] = len(records) == 28
        prepared["memory_after"] = memory_snapshot(run_id)
    except Exception as error:
        status["failure_type"] = type(error).__name__
        raise
    finally:
        for process in processes:
            try:
                process.close()
            except Exception as error:
                status.update(complete=False, adapter_stop_failure_type=type(error).__name__)
        try:
            if old.state_path(run_id).exists():
                server, lines = collect(run_id, mode)
                (folder / "server.json").write_text(json.dumps(server))
                (folder / "server-log-lines.txt").write_text("\n".join(lines) + "\n")
        except Exception as error:
            status.update(complete=False, evidence_failure_type=type(error).__name__)
        finally:
            removed = cleanup(run_id)
            (folder / "cleanup.json").write_text(json.dumps(removed, indent=2))
            if not removed["absence_proved"]:
                status["complete"] = False
            prepared["completed_tasks"] = len(records)
            (folder / "prepared.json").write_text(json.dumps(prepared, indent=2))
            status.update(completed=len(records), passed=sum(row["passed"] for row in records),
                          cleanup_absence_proved=removed["absence_proved"])
            if status["complete"]:
                status["sha256"] = {name: sha(folder / name) for name in
                    ("tasks.jsonl", "prepared.json", "server.json", "server-log-lines.txt", "cleanup.json")}
            (folder / "sealed.json").write_text(json.dumps(status, indent=2))
    if not status["complete"]:
        raise RuntimeError("Utility evidence is incomplete")
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("separate", "immutable"))
    args = parser.parse_args()
    with (P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(execute_arm(HERE / "data/u1", args.mode)), flush=True)


if __name__ == "__main__":
    main()
