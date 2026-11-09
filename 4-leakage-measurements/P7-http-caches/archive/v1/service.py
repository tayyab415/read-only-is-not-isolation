"""Owned P7 runtime preparation, bounded requests, and cleanup."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import configuration
from request_guard import RequestGuard

HERE = Path(__file__).resolve().parent
P2 = HERE.parent / "P2-residual"
sys.path.insert(0, str(P2))
previous_plan = sys.modules.pop("plan", None)
spec = importlib.util.spec_from_file_location("p7_p2_service", P2 / "service.py")
p2 = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(p2)
finally:
    sys.path.remove(str(P2))
    sys.modules.pop("plan", None)
    if previous_plan is not None:
        sys.modules["plan"] = previous_plan
old = p2.old
IMAGE = "p7-cache-fixtures:nginx-1.30.6-squid-7.8"
RUN = re.compile(r"p7-g1-(nginx|squid)-(20|150)-(shared|tenant-keyed|private|disabled|no-sender)-(cal|hold)")
PAYLOAD_SHA = hashlib.sha256(b"J controls harmless artifact fixture v1\n").hexdigest()
MISSING_SHA = hashlib.sha256(b"Missing harmless fixture\n").hexdigest()
CLIENT = """
import hashlib,http.client,json,sys,time
job=json.loads(sys.stdin.readline())
started=time.time_ns(); monotonic=time.monotonic_ns()
record={**job,"start_utc_ns":started,"status":None}
connection=None
try:
    if job["execute"]==1:
        connection=http.client.HTTPConnection(job["host"],8000,timeout=5)
        connection.request(job["method"],job["target"],headers={"User-Agent":job["marker"],"Connection":"close",**job["headers"]})
        response=connection.getresponse()
        record["response_headers_ms"]=(time.monotonic_ns()-monotonic)/1e6
        body=response.read(65537)
        if len(body)>65536: raise RuntimeError("Fixture response is too large")
        record.update(status=response.status,response_headers=response.getheaders(),body_bytes=len(body),
            body_sha256=hashlib.sha256(body).hexdigest(),body_text=body.decode(errors="replace"))
    else:
        record["idle"]=True
except Exception as error:
    record["failure_type"]=type(error).__name__
finally:
    if connection is not None: connection.close()
record.update(end_utc_ns=time.time_ns(),elapsed_ms=(time.monotonic_ns()-monotonic)/1e6)
print(json.dumps(record),flush=True)
"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sources():
    freeze = json.loads((HERE / "SOURCES-FROZEN.json").read_text())
    for name, expected in freeze["sha256"].items():
        if sha(HERE / name) != expected:
            raise RuntimeError("Frozen P7 source changed")
    for name, expected in freeze["import_sha256"].items():
        if sha(HERE.parent / name) != expected:
            raise RuntimeError("Frozen P7 imported source changed")
    p2.check_sources()
    return freeze


def build_check(require_build_record=True):
    """Read actual binaries and build flags from the prepared local image."""
    check_sources()
    result = old.run(["docker", "image", "inspect", IMAGE])
    if result.returncode:
        raise RuntimeError("Owned P7 image has not been built")
    image = json.loads(result.stdout)[0]
    if image.get("Config", {}).get("Labels", {}).get("org.research.source-freeze.sha256") \
            != sha(HERE / "SOURCES-FROZEN.json"):
        raise RuntimeError("The cache image was built from another source freeze")
    script = ("import hashlib,json,subprocess\n"
              "paths={'nginx':'/opt/p7/nginx/sbin/nginx','squid':'/opt/p7/squid/sbin/squid'}\n"
              "result={}\n"
              "for name,path in paths.items():\n"
              " p=subprocess.run([path,'-V' if name=='nginx' else '-v'],capture_output=True,text=True)\n"
              " result[name]={'version':p.stdout+p.stderr,'exit_code':p.returncode,'binary_sha256':hashlib.sha256(open(path,'rb').read()).hexdigest()}\n"
              "print(json.dumps(result))")
    binaries = json.loads(old.run_ok(["docker", "run", "--rm", "--name", "p7-binary-readback", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--memory", "128m",
        "--memory-swap", "128m", "--cpus", "0.5", "--pids-limit", "48", IMAGE, "python3", "-c", script]))
    if any(row["exit_code"] for row in binaries.values()) \
            or "nginx/1.30.6" not in binaries["nginx"]["version"] \
            or "Squid Cache: Version 7.8" not in binaries["squid"]["version"] \
            or "--enable-http-violations" not in binaries["squid"]["version"]:
        raise RuntimeError("Actual cache binaries do not satisfy the declared feature scope")
    output = {"image_id": image["Id"], "repo_digests": image.get("RepoDigests", []),
              "binaries": binaries, "image_logical_size_bytes": image.get("Size"),
              "source_freeze_sha256": sha(HERE / "SOURCES-FROZEN.json")}
    if require_build_record:
        record = json.loads((HERE / "build/g1/build-result.json").read_text())
        if not record["complete"] or not record["builder_cleanup"]["absence_proved"] \
                or record["source_freeze_sha256"] != output["source_freeze_sha256"] \
                or record["readback"]["image_id"] != output["image_id"] \
                or record["readback"]["binaries"] != output["binaries"]:
            raise RuntimeError("No complete matching build and cleanup evidence exists")
    return output


def container(name, network, memory, pids, mounts=(), image="python:3.12-slim", command=None):
    command = command or ("python3", "-c", "import time; time.sleep(86400)")
    old.run_ok(["docker", "run", "-d", "--name", name, "--network", network,
        "--user", "20001:20001", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--memory", str(memory),
        "--memory-swap", str(memory), "--cpus", "0.5", "--pids-limit", str(pids),
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", *mounts, image, *command])


class Runtime:
    def __init__(self, run_id, product, arm, folder, guard):
        if not RUN.fullmatch(run_id) or product not in configuration.PRODUCTS \
                or arm not in (*configuration.ARMS, "no-sender"):
            raise ValueError("Unknown owned cache runtime")
        self.run_id, self.product, self.arm = run_id, product, arm
        self.effective_arm = "shared" if arm == "no-sender" else arm
        self.folder, self.guard = Path(folder), guard
        self.networks = {role: run_id + "-net-" + role for role in ("alice", "bob", "services")}
        self.clients = {actor: run_id + "-" + actor for actor in ("alice", "bob")}
        self.origin = run_id + "-origin"
        self.cache_names = {actor: run_id + "-cache-" + actor for actor in ("alice", "bob")} \
            if arm == "private" else {actor: run_id + "-cache" for actor in ("alice", "bob")}
        self.processes = []
        self.streams = []
        self.public = {"run_id": run_id, "product": product, "arm": arm, "effective_arm": self.effective_arm,
                       "networks": self.networks, "clients": self.clients, "origin": self.origin,
                       "caches": self.cache_names, "prepared": False}

    def prepare(self):
        p2.check_gate()
        self.public["build"] = build_check()
        if old.free_gib() < 2:
            raise RuntimeError("Insufficient memory for a bounded cache cohort")
        self.folder.mkdir(parents=True, exist_ok=True)
        for network in self.networks.values():
            old.docker_network(network)
        for actor, name in self.clients.items():
            container(name, self.networks[actor], 134217728, 48)
        container(self.origin, self.networks["services"], 134217728, 48,
            mounts=("-v", f"{HERE / 'origin.py'}:/origin.py:ro"), command=("python3", "-u", "/origin.py"))
        ips = {actor: old.ip_address(name, self.networks[actor]) for actor, name in self.clients.items()}
        for name in sorted(set(self.cache_names.values())):
            actors = [actor for actor, selected in self.cache_names.items() if selected == name]
            origins = {ips[actor]: actor for actor in actors}
            config_dir = self.folder / name
            config_dir.mkdir()
            (config_dir / "origins.json").write_text(json.dumps(origins))
            configuration_text = getattr(configuration, self.product)(self.effective_arm, origins, self.origin)
            config_path = config_dir / "cache.conf"
            config_path.write_text(configuration_text)
            container(name, self.networks[actors[0]], 268435456 if self.product == "squid" else 134217728,
                96 if self.product == "squid" else 48,
                mounts=("-v", f"{config_dir}:/config:ro", "-v", f"{HERE / 'store_id.py'}:/store_id.py:ro"), image=IMAGE)
            for network in [*(self.networks[actor] for actor in actors[1:]), self.networks["services"]]:
                old.run_ok(["docker", "network", "connect", network, name])
            if self.product == "nginx":
                command = ["/opt/p7/nginx/sbin/nginx", "-c", "/config/cache.conf", "-g", "daemon off;"]
                parsed = old.run(["docker", "exec", name, "/opt/p7/nginx/sbin/nginx", "-t", "-c", "/config/cache.conf"])
            else:
                command = ["/opt/p7/squid/sbin/squid", "-N", "-f", "/config/cache.conf"]
                parsed = old.run(["docker", "exec", name, "/opt/p7/squid/sbin/squid", "-k", "parse", "-f", "/config/cache.conf"])
            (config_dir / "parse.txt").write_text(parsed.stdout + parsed.stderr)
            if parsed.returncode:
                raise RuntimeError("Cache configuration did not parse")
            if self.product == "squid":
                initialized = old.run(["docker", "exec", name, "/opt/p7/squid/sbin/squid", "-z", "-f", "/config/cache.conf"])
                (config_dir / "initialize.txt").write_text(initialized.stdout + initialized.stderr)
                if initialized.returncode:
                    raise RuntimeError("Private Squid cache initialization failed")
            stdout, stderr = (config_dir / "stdout.txt").open("w"), (config_dir / "stderr.txt").open("w")
            self.streams.extend((stdout, stderr))
            process = subprocess.Popen(["docker", "exec", name, *command], stdout=stdout, stderr=stderr)
            self.processes.append((name, process))
            old.wait_ready(self.clients[actors[0]], name)
            if process.poll() is not None:
                raise RuntimeError("Cache process stopped during readiness")
            self.public.setdefault("configurations", {})[name] = {
                "origins": origins, "configuration": configuration_text, "configuration_sha256": sha(config_path),
                "parse_exit_code": parsed.returncode, "process_pid_on_controller": process.pid}
        self.public["boundary"] = self.boundary()
        self.public["network_controls"] = self.network_controls()
        self.public["resource_before"] = self.resource_snapshot()
        self.public["prepared"] = True
        return self.public

    def boundary(self):
        names = [*self.clients.values(), self.origin, *sorted(set(self.cache_names.values()))]
        output = {"containers": {}, "networks": {}}
        for name in names:
            item = json.loads(old.run_ok(["docker", "inspect", name]))[0]
            host, config = item["HostConfig"], item["Config"]
            forbidden = any(row.split("=", 1)[0].startswith(("AWS_", "BEDROCK_")) for row in config.get("Env", []))
            actor = next((actor for actor, selected in self.clients.items() if selected == name), None)
            is_cache = name in self.cache_names.values()
            attached = {self.networks[actor]} if actor else {self.networks["services"]} if name == self.origin else \
                {self.networks[who] for who, cache in self.cache_names.items() if cache == name} | {self.networks["services"]}
            memory = 268435456 if is_cache and self.product == "squid" else 134217728
            pids = 96 if is_cache and self.product == "squid" else 48
            row = {"user": config["User"], "root_read_only": host["ReadonlyRootfs"],
                "cap_drop": host["CapDrop"], "security_options": host["SecurityOpt"],
                "memory": host["Memory"], "memory_swap": host["MemorySwap"],
                "nano_cpus": host["NanoCpus"], "pids_limit": host["PidsLimit"], "tmpfs": host["Tmpfs"],
                "forbidden_environment_present": forbidden, "image_id": item["Image"],
                "published_ports": item["NetworkSettings"]["Ports"],
                "network_addresses": {key: value["IPAddress"] for key, value in item["NetworkSettings"]["Networks"].items()},
                "bind_mount_destinations": [mount["Destination"] for mount in item["Mounts"] if mount["Type"] == "bind"],
                "mounts_read_only": all(not mount["RW"] for mount in item["Mounts"] if mount["Type"] == "bind")}
            if row["user"] != "20001:20001" or not row["root_read_only"] or forbidden \
                    or row["memory"] != row["memory_swap"] or row["memory"] != memory \
                    or row["nano_cpus"] != 500000000 or row["pids_limit"] != pids \
                    or "ALL" not in row["cap_drop"] or "no-new-privileges:true" not in row["security_options"] \
                    or any(row["published_ports"].values()) or not row["mounts_read_only"] \
                    or set(row["network_addresses"]) != attached \
                    or actor and row["bind_mount_destinations"]:
                raise RuntimeError("Declared cache boundary differs from its readback")
            output["containers"][name] = row
        for name in self.networks.values():
            item = json.loads(old.run_ok(["docker", "network", "inspect", name]))[0]
            row = {"internal": item["Internal"], "options": item["Options"]}
            if not row["internal"] or row["options"].get("com.docker.network.bridge.gateway_mode_ipv4") != "isolated":
                raise RuntimeError("Cache network boundary differs")
            output["networks"][name] = row
        return output

    def network_controls(self):
        # Reuse the known live-listener probe, with only these owned names.
        listener = subprocess.Popen(["docker", "exec", "-i", self.clients["bob"], "python3", "-u", "-c", p2.preflight.LISTENER],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        records = []
        try:
            import select
            if not select.select([listener.stdout], [], [], 5)[0] or not json.loads(listener.stdout.readline())["ready"]:
                raise RuntimeError("Owned peer listener failed")
            origin_ip = old.ip_address(self.origin, self.networks["services"])
            peer_ip = old.ip_address(self.clients["bob"], self.networks["bob"])
            for actor in ("alice", "bob"):
                jobs = [{"name": "assigned cache", "host": self.cache_names[actor], "port": 8000, "expected_connected": True},
                        {"name": "direct origin", "host": origin_ip, "port": 8000, "expected_connected": False},
                        {"name": "live peer listener", "host": peer_ip, "port": 18888, "expected_connected": actor == "bob"}]
                result = old.run(["docker", "exec", "-i", self.clients[actor], "python3", "-c", p2.preflight.PROBE],
                                 input=json.dumps(jobs), timeout=8)
                if result.returncode:
                    raise RuntimeError("Owned cache route probe failed")
                rows = json.loads(result.stdout)
                if len(rows) != len(jobs) or any(any(row.get(key) != value for key, value in job.items())
                                               for row, job in zip(rows, jobs)):
                    raise RuntimeError("Cache probe differs from its assigned targets")
                records.extend({**row, "actor": actor} for row in rows)
            output = p2.preflight.check_results(records)
        finally:
            try:
                listener.stdin.write("stop\n")
                listener.stdin.flush()
            except BrokenPipeError:
                pass
            listener.stdin.close()
            try:
                listener.wait(timeout=3)
            except subprocess.TimeoutExpired:
                listener.kill()
                listener.wait(timeout=3)
            listener.stdout.close()
            listener.stderr.close()
        if listener.returncode:
            raise RuntimeError("Owned listener stop was not clean")
        return {**output, "listener_stopped": True, "listener_exit_code": 0}

    def record_request(self, record):
        with (self.folder / "requests.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def request(self, actor, method, path, marker, headers=None, execute=True):
        if actor not in self.clients or method not in ("GET", "HEAD") \
                or not re.fullmatch(r"/(?:missing/(20|150)|present)/[a-zA-Z0-9_.-]+", path) \
                or headers and (set(headers) - {"Range", "If-None-Match", "If-Modified-Since"}
                               or any(not isinstance(value, str) or any(c in value for c in "\r\n") for value in headers.values())):
            raise ValueError("Request leaves the declared owned grammar")
        if type(execute) is not bool:
            raise ValueError("Invalid private sender execution flag")
        if execute:
            self.guard.reserve(marker)
        target = f"http://{self.origin}:8000{path}" if self.product == "squid" else path
        job = {"actor": actor, "method": method, "path": path, "target": target,
               "host": self.cache_names[actor], "marker": marker, "headers": headers or {},
               "execute": 1 if execute else 0}
        result = old.run(["docker", "exec", "-i", self.clients[actor], "python3", "-c", CLIENT],
                         input=json.dumps(job), timeout=8)
        if result.returncode:
            raise RuntimeError("Cache client worker failed")
        record = json.loads(result.stdout)
        self.record_request(record)
        if any(record.get(key) != value for key, value in job.items()):
            raise RuntimeError("Cache request result differs from its reserved job")
        return record

    def rejected_origin_request(self, marker, path):
        """The trusted origin can reach the cache network but has no client identity."""
        self.guard.reserve(marker)
        target = f"http://{self.origin}:8000{path}" if self.product == "squid" else path
        job = {"actor": "trusted-source-control", "method": "GET", "path": path, "target": target,
               "host": self.cache_names["alice"], "marker": marker, "headers": {}, "execute": 1}
        result = old.run(["docker", "exec", "-i", self.origin, "python3", "-c", CLIENT],
                         input=json.dumps(job), timeout=8)
        if result.returncode:
            raise RuntimeError("Unknown-origin HTTP control failed")
        row = json.loads(result.stdout)
        self.record_request(row)
        if any(row.get(key) != value for key, value in job.items()) or row["status"] != 403:
            raise RuntimeError("Unknown cache origin was not rejected")
        return row

    def capture(self):
        output = {"run_id": self.run_id, "origin_log": old.docker_log(self.origin), "caches": {}}
        for name in sorted(set(self.cache_names.values())):
            def read(path):
                result = old.run(["docker", "exec", name, "cat", path])
                return result.stdout if result.returncode == 0 else None
            inventory = old.run(["docker", "exec", name, "python3", "-c",
                "from pathlib import Path; import json; "
                "files=[p for p in Path('/tmp/cache').rglob('*') if p.is_file()]; "
                "print(json.dumps({'files':len(files),'file_bytes':sum(p.stat().st_size for p in files),"
                "'allocated_blocks_bytes':sum(p.stat().st_blocks*512 for p in files),"
                "'method':'Private cache-directory inventory after native cache stop; not peak storage.'}))"])
            output["caches"][name] = {"access_log": read("/tmp/cache-access.jsonl" if self.product == "nginx" else "/tmp/cache-access.txt"),
                "store_id_log": read("/tmp/store-id.jsonl") if self.effective_arm == "tenant-keyed" and self.product == "squid" else None,
                "native_log": read("/tmp/nginx-error.log" if self.product == "nginx" else "/tmp/squid-cache.log"),
                "cache_inventory": json.loads(inventory.stdout) if inventory.returncode == 0 else None}
        return output

    def resource_snapshot(self):
        names = [*self.clients.values(), self.origin, *sorted(set(self.cache_names.values()))]
        text = old.run_ok(["docker", "stats", "--no-stream", "--format", "{{json .}}", *names])
        return {"utc_ns": time.time_ns(), "method": "Docker CLI usage snapshots, not peak memory or process RSS",
                "records": [{key: row.get(key) for key in ("Name", "MemUsage", "CPUPerc", "PIDs", "NetIO", "BlockIO")}
                            for row in (json.loads(line) for line in text.splitlines())]}

    def stop_cache_processes(self):
        records = []
        for name, process in self.processes:
            binary = "/opt/p7/nginx/sbin/nginx" if self.product == "nginx" else "/opt/p7/squid/sbin/squid"
            args = [binary, "-s", "quit", "-c", "/config/cache.conf"] if self.product == "nginx" \
                else [binary, "-k", "shutdown", "-f", "/config/cache.conf"]
            result = old.run(["docker", "exec", name, *args])
            record = {"name": name, "signal_exit_code": result.returncode, "process_exit_code": None}
            try:
                process.wait(timeout=5)
                record["process_exit_code"] = process.returncode
            except subprocess.TimeoutExpired:
                record["stop_timeout"] = True
            records.append(record)
        return {"passed": all(row["signal_exit_code"] == row["process_exit_code"] == 0 for row in records),
                "records": records}

    def close(self):
        names = [*self.clients.values(), self.origin, *sorted(set(self.cache_names.values()))]
        for name in names:
            old.run(["docker", "rm", "-f", name])
        for network in self.networks.values():
            old.run(["docker", "network", "rm", network])
        for _, process in self.processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in self.streams:
            stream.close()
        remaining_names = old.run_ok(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
        remaining_networks = old.run_ok(["docker", "network", "ls", "--format", "{{.Name}}"]).splitlines()
        left_containers = [name for name in remaining_names if name.startswith(self.run_id + "-")]
        left_networks = [name for name in remaining_networks if name.startswith(self.run_id + "-")]
        return {"run_id": self.run_id, "containers_readback": left_containers, "networks_readback": left_networks,
                "absence_proved": not left_containers and not left_networks, "artifactory_touched": False}
