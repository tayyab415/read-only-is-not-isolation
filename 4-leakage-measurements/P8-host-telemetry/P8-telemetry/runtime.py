"""Owned native runtime. All mutations are gated and use fresh P8 names."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import sys

from experiment import masked, static_view

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
spec = importlib.util.spec_from_file_location("p8_imported_p2_service", P2 / "service.py")
p2 = importlib.util.module_from_spec(spec)
saved_path = sys.path[:]
try:
    sys.path.insert(0, str(P2))
    spec.loader.exec_module(p2)
finally:
    sys.path[:] = saved_path
old = p2.old


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sources():
    frozen = json.loads((HERE / "SOURCES-FROZEN.json").read_text())
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError("A frozen P8 source changed")
    for name, expected in frozen["import_sha256"].items():
        path = (HERE.parent / name).resolve()
        if HERE.parent not in path.parents or sha(path) != expected:
            raise RuntimeError("A frozen P8 import changed")
    p2.check_sources()
    return frozen


def check_gate():
    check_sources()
    p2.check_gate()
    allow = json.loads((HERE / "ALLOW-COLLECTION.json").read_text())
    if allow.get("p2_collection_closed_and_audited") is not True \
            or allow["p8_source_freeze_sha256"] != sha(HERE / "SOURCES-FROZEN.json"):
        raise RuntimeError("The P8 quiet-window gate does not pass")
    required = {"p2-holdout-audit.json"} | {f"p2-holdout-seal-{name}.json"
                                           for name in p2.plan.CONFIGURATIONS}
    if set(allow["evidence_sha256"]) != required:
        raise RuntimeError("The P8 gate lacks complete P2 evidence")
    for name, expected in allow["evidence_sha256"].items():
        if sha(HERE / "gate-evidence" / name) != expected:
            raise RuntimeError("P8 gate evidence changed")
    audit = json.loads((HERE / "gate-evidence/p2-holdout-audit.json").read_text())
    if not audit["passed"] or len(audit["runs"]) != 4 \
            or any(not row["passed"] or row["trials"] != 1000 for row in audit["runs"]):
        raise RuntimeError("P2 has no complete audited holdout")
    for name in p2.plan.CONFIGURATIONS:
        seal = json.loads((HERE / "gate-evidence" / f"p2-holdout-seal-{name}.json").read_text())
        if seal.get("complete") is not True or seal.get("cleanup_absence_proved") is not True \
                or seal.get("trials") != 1000:
            raise RuntimeError("A P2 holdout or cleanup is incomplete")
    names = old.run_ok(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    if any(name.startswith(("p1-p2-m1-", "p1-p8-")) for name in names):
        raise RuntimeError("Another P2/P8 runtime exists")
    if old.free_gib() < 4:
        raise RuntimeError("P8 requires at least 4 GiB available memory")
    return allow


def guest_program():
    # Modules enter a fixed process argument. No host source bind, writable
    # root, arbitrary shell command, labels in receiver code, or secret is used.
    result = "import base64,sys,types\n"
    for name in ("observer_contract", "telemetry", "participant"):
        encoded = base64.b64encode((HERE / f"{name}.py").read_bytes()).decode()
        result += f"m=types.ModuleType({name!r});sys.modules[{name!r}]=m\n"
        result += f"exec(compile(base64.b64decode({encoded!r}),{name!r},'exec'),m.__dict__)\n"
    return result + "sys.modules['participant'].main()\n"


def check_smoke():
    data = HERE / "data/smoke-p8-s1"
    audit = json.loads((data / "smoke-audit-results.json").read_text())
    if not audit.get("passed") or len(audit["runs"]) != 4 \
            or set(row["configuration"] for row in audit["runs"]) != set(
                ("native", "masked", "aa-native", "aa-masked")) \
            or audit["audit_source_sha256"] != sha(HERE / "audit.py"):
        raise RuntimeError("P8 requires a passing fresh native smoke")
    for row in audit["runs"]:
        folder = data / row["configuration"]
        if not row["passed"] or row["trials"] != 4 \
                or sha(folder / "smoke.jsonl") != row["observation_sha256"] \
                or sha(folder / "smoke-sealed.json") != row["seal_sha256"]:
            raise RuntimeError("A P8 native smoke is incomplete or changed")
        seal = json.loads((folder / "smoke-sealed.json").read_text())
        if set(seal["evidence_sha256"]) != {
                f"smoke-{name}.json" for name in ("schedule", "provision", "readiness", "server", "cleanup")}:
            raise RuntimeError("P8 smoke lacks its full evidence inventory")
        for name, expected in seal["evidence_sha256"].items():
            if sha(folder / name) != expected:
                raise RuntimeError("A P8 native smoke artifact changed")
    images = set()
    for row in audit["runs"]:
        public = json.loads((data / row["configuration"] / "smoke-provision.json").read_text())
        images.update(item["image_id"] for item in public["boundary"]["containers"].values())
    current = old.run_ok(["docker", "image", "inspect", "python:3.12-slim", "--format", "{{.Id}}"]).strip()
    if images != {current}:
        raise RuntimeError("The native smoke and main runtime images differ")
    return audit


def validate_boundary(boundary, state, configuration, mask_path):
    if set(boundary["containers"]) != {"alice", "bob", "mirror_alice", "mirror_bob"}:
        raise RuntimeError("Unexpected P8 container inventory")
    images = {row["image_id"] for row in boundary["containers"].values()}
    if len(images) != 1 or not next(iter(images)).startswith("sha256:"):
        raise RuntimeError("P8 participants and mirrors must use the same image")
    for role, row in boundary["containers"].items():
        if row["user"] != "20001:20001" or not row["root_read_only"] \
                or row["cap_drop"] != ["ALL"] or row["memory"] != 128 * 1024 ** 2 \
                or row["memory_swap"] != row["memory"] or row["nano_cpus"] != 500_000_000 \
                or row["pids_limit"] != 48 or row["forbidden_environment_present"] \
                or "no-new-privileges:true" not in row["security_options"] \
                or row["privileged"] or row["pid_mode"] or row["ipc_mode"] == "host" \
                or any(row["published_ports"].values()):
            raise RuntimeError("A P8 sandbox privilege or limit changed")
        expected_network = state["networks"]["alice" if "alice" in role else "bob"]
        if row["attached_networks"] != [expected_network] \
                or any(mount["rw"] for mount in row["bind_mounts"]):
            raise RuntimeError("A P8 network or mount differs")
        if role in ("alice", "bob"):
            expected = [{"source": str(mask_path), "destination": "/proc/stat", "rw": False}] \
                if role == "bob" and masked(configuration) else []
            if row["bind_mounts"] != expected or row["cgroupns_mode"] != "private" \
                    or row["tmpfs"] != {"/tmp": "rw,nosuid,nodev,size=16m"}:
                raise RuntimeError("A client view differs from its assigned intervention")
        else:
            if {mount["destination"] for mount in row["bind_mounts"]} != {
                    "/mirror.py", "/deploy.py", "/catalog", "/config.json"} \
                    or row["tmpfs"] != {"/tmp": "rw,nosuid,nodev,size=64m"}:
                raise RuntimeError("An immutable mirror deployment changed")
    for role, row in boundary["networks"].items():
        names = sorted(state["containers"][name] for name in (role, "mirror_" + role))
        if not row["internal"] or row["options"].get(
                "com.docker.network.bridge.gateway_mode_ipv4") != "isolated" \
                or row["containers"] != names:
            raise RuntimeError("A network is not the private assigned network")


def boundary_readback(state):
    result = {"containers": {}, "networks": {}}
    for role, name in state["containers"].items():
        item = json.loads(old.run_ok(["docker", "inspect", name]))[0]
        host, config = item["HostConfig"], item["Config"]
        result["containers"][role] = {
            "name": name, "user": config["User"], "root_read_only": host["ReadonlyRootfs"],
            "cap_drop": host["CapDrop"], "security_options": host["SecurityOpt"],
            "privileged": host["Privileged"], "pid_mode": host["PidMode"],
            "ipc_mode": host["IpcMode"], "cgroupns_mode": host["CgroupnsMode"],
            "memory": host["Memory"], "memory_swap": host["MemorySwap"],
            "nano_cpus": host["NanoCpus"], "pids_limit": host["PidsLimit"],
            "tmpfs": host["Tmpfs"], "published_ports": item["NetworkSettings"]["Ports"],
            "attached_networks": list(item["NetworkSettings"]["Networks"]),
            "network_addresses": {network: row["IPAddress"] for network, row in
                                  item["NetworkSettings"]["Networks"].items()},
            "image_id": item["Image"], "masked_paths": host.get("MaskedPaths"),
            "readonly_paths": host.get("ReadonlyPaths"),
            "forbidden_environment_present": any(
                value.split("=", 1)[0].startswith(("AWS_", "BEDROCK_"))
                for value in config.get("Env", [])),
            "bind_mounts": [{"source": mount["Source"], "destination": mount["Destination"],
                             "rw": mount["RW"]} for mount in item["Mounts"] if mount["Type"] == "bind"]}
    for role, name in state["networks"].items():
        item = json.loads(old.run_ok(["docker", "network", "inspect", name]))[0]
        result["networks"][role] = {"name": name, "internal": item["Internal"],
                                   "options": item["Options"], "containers": sorted(
                                       row["Name"] for row in item["Containers"].values())}
    return result


def provision(run_id, configuration):
    old.check_id(run_id)
    old.ensure_dirs()
    if old.state_path(run_id).exists():
        raise RuntimeError("P8 requires a fresh owned run identifier")
    prefix = f"p1-{run_id}-"
    networks = {actor: prefix + f"net-{actor}" for actor in ("alice", "bob")}
    containers = {actor: prefix + actor for actor in ("alice", "bob")}
    containers.update({f"mirror_{actor}": prefix + f"mirror-{actor}" for actor in ("alice", "bob")})
    users = {actor: prefix + actor for actor in ("alice", "bob")}
    config_dir = HERE / "runtime-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    mask_path = config_dir / f"stat-{run_id}.txt"
    mask_path.write_bytes(static_view(os.cpu_count()))
    mask_path.chmod(0o444)
    state = {"run_id": run_id, "kind": "immutable", "configuration": configuration,
             "networks": networks, "containers": containers, "users": users,
             "repositories": users.copy(), "repository_names_are": "synthetic origin identities",
             "manifest_sha256": sha(p2.P3 / "data/manifest.json")}
    # Write owned inventory before mutations so failure cleanup has all names.
    old.state_path(run_id).write_text(json.dumps(state))
    for actor in ("alice", "bob"):
        old.docker_network(networks[actor])
        extra = ["--cgroupns", "private"]
        if actor == "bob" and masked(configuration):
            extra += ["--mount", f"type=bind,source={mask_path},target=/proc/stat,readonly"]
        old.sandbox(containers[actor], networks[actor], extra=tuple(extra))
        config = {"tenant": actor, "queue_size": 5,
                  "origin_binding": {old.ip_address(containers[actor], networks[actor]): users[actor]},
                  "mirror_source_sha256": sha(p2.P3 / "mirror.py"),
                  "manifest_sha256": state["manifest_sha256"]}
        path = config_dir / f"mirror-{run_id}-{actor}.json"
        path.write_text(json.dumps(config))
        p2.mirror_sandbox(containers[f"mirror_{actor}"], networks[actor],
                          ("-v", f"{p2.P3 / 'mirror.py'}:/mirror.py:ro",
                           "-v", f"{p2.P3_DEPLOY / 'deploy.py'}:/deploy.py:ro",
                           "-v", f"{p2.P3 / 'data'}:/catalog:ro", "-v", f"{path}:/config.json:ro"),
                          ("python3", "/deploy.py", "--mirror-source", "/mirror.py",
                           "--manifest", "/catalog/manifest.json", "--config", "/config.json",
                           "--log", "/tmp/base.jsonl", "--wire-log", "/tmp/wire.jsonl"))
        old.wait_ready(containers[actor], containers[f"mirror_{actor}"])
    boundary = boundary_readback(state)
    validate_boundary(boundary, state, configuration, mask_path)
    controls = p2.preflight.network_checks(old, run_id, config_dir / f"network-{run_id}.json")
    return {**state, "boundary": boundary, "network_controls": controls,
            "mask_path": str(mask_path), "mask_bytes_b64": base64.b64encode(mask_path.read_bytes()).decode(),
            "mask_sha256": sha(mask_path),
            "docker_version": json.loads(old.run_ok(["docker", "version", "--format", "{{json .}}"])),
            "runtime_accounting": old.run_ok(["docker", "info", "--format",
                                             "{{json .CgroupDriver}} {{json .CgroupVersion}}"]),
            "source_freeze_sha256": sha(HERE / "SOURCES-FROZEN.json")}


class Participant:
    def __init__(self, state, actor):
        self.actor = actor
        self.process = subprocess.Popen(
            ["docker", "exec", "-i", state["containers"][actor], "python3", "-u", "-c", guest_program()],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        config = {"actor": actor, "run_id": state["run_id"], "host": state["containers"][f"mirror_{actor}"]}
        self.send(config)
        try:
            self.readiness = self.read(30)
            if self.readiness.get("ready") is not True:
                raise RuntimeError("P8 participant not ready")
        except Exception:
            self.close()
            raise

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def read(self, timeout=15):
        ready, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not ready:
            raise RuntimeError("P8 participant timed out")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("P8 participant exited")
        return json.loads(line)

    def close(self):
        try:
            self.send({"op": "exit"})
            self.process.wait(timeout=3)
            if self.process.returncode:
                raise RuntimeError("P8 participant did not stop cleanly")
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=3)
            raise RuntimeError("P8 participant stop required termination")
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
