#!/usr/bin/env python3
"""Twenty-eight fresh dependency-resolving installs from frozen local serving."""
import hashlib
import json
import os
import random
import select
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CHECKS = {
    "requests": ("requests", "assert requests.Request('GET', 'https://example.invalid/', params={'x':1}).prepare().url == 'https://example.invalid/?x=1'"),
    "rich": ("rich", "from rich.text import Text; assert Text.from_markup('[bold]ok[/bold]').plain == 'ok'"),
    "python-dateutil": ("dateutil", "from dateutil.parser import isoparse; assert isoparse('2026-11-08T12:00:00Z').year == 2026"),
    "networkx": ("networkx", "assert networkx.shortest_path(networkx.path_graph(3), 0, 2) == [0,1,2]"),
    "click": ("click", "from click.testing import CliRunner; c=click.Command('ok', callback=lambda: click.echo('ok')); r=CliRunner().invoke(c); assert r.exit_code == 0 and r.output == 'ok\\n'"),
    "packaging": ("packaging", "from packaging.version import Version; assert Version('2.1') > Version('2.0')"),
    "attrs": ("attrs", "C=attrs.make_class('Thing', {'x':attrs.field()}); assert C(3).x == 3"),
    "typing_extensions": ("typing_extensions", "class_name=typing_extensions.TypedDict('Thing', {'x':int}); assert class_name(x=3)['x'] == 3"),
    "pytest": ("pytest", "assert pytest.approx(0.3) == 0.1 + 0.2"),
    "beautifulsoup4": ("bs4", "assert bs4.BeautifulSoup('<p>ok</p>', 'html.parser').p.text == 'ok'"),
    "more-itertools": ("more_itertools", "assert list(more_itertools.chunked([1,2,3], 2)) == [[1,2],[3]]"),
    "toolz": ("toolz", "assert list(toolz.unique([1,2,1])) == [1,2]"),
    "tenacity": ("tenacity", "f=tenacity.retry(stop=tenacity.stop_after_attempt(1))(lambda: 3); assert f() == 3"),
    "pluggy": ("pluggy", "p=pluggy.PluginManager('demo'); assert p.register(object(), name='x') == 'x'"),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def start_service(tenant):
    process = subprocess.Popen([sys.executable, "-u", str(HERE / "mirror.py"),
                                "--tenant", tenant, "--log", str(DATA / f"service-{tenant}.jsonl")],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not select.select([process.stdout], [], [], 30)[0]:
        process.kill()
        raise RuntimeError("Mirror did not become ready")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError(f"Mirror startup failed: {process.stderr.read()[-500:]}")
    ready = json.loads(line)
    if not ready.get("ready"):
        raise RuntimeError("Mirror startup lacked its readiness record")
    return process, ready


def closed(port):
    with socket.socket() as connection:
        connection.settimeout(2)
        return connection.connect_ex(("127.0.0.1", port)) != 0


def main():
    output = DATA / "utility-results.json"
    if output.exists() or (DATA / "utility-frozen.json").exists():
        raise SystemExit("Utility run already exists. Retain it; use a new run directory.")
    manifest_path = DATA / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    frozen = {"utc_ns": time.time_ns(), "tasks": 28, "randomization_seed": 20261108,
              "sha256": {name: sha(HERE / name) for name in
                         ["utility.py", "mirror.py", "requirements.txt", "constraints.txt", "PROTOCOL.md"]},
              "manifest_sha256": sha(manifest_path)}
    (DATA / "utility-frozen.json").write_text(json.dumps(frozen, indent=2) + "\n")
    requirements = [line.strip() for line in (HERE / "requirements.txt").read_text().splitlines() if line.strip()]
    tasks = [(tenant, requirement) for tenant in ["alice", "bob"] for requirement in requirements]
    random.Random(20261108).shuffle(tasks)
    services, readiness, outcomes = {}, {}, []
    before = {entry["path"]: sha(DATA / entry["path"]) for entry in manifest["entries"]}
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
    environment.update(PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1")
    rss_before, rss_after = {}, {}
    try:
        for tenant in ["alice", "bob"]:
            services[tenant], readiness[tenant] = start_service(tenant)
            rss_before[tenant] = int(subprocess.run(
                ["ps", "-o", "rss=", "-p", str(services[tenant].pid)],
                capture_output=True, text=True, check=True).stdout.strip())
        for index, (tenant, requirement) in enumerate(tasks):
            name, version = requirement.split("==")
            task_id = f"{index:02d}-{tenant}-{name}"
            task_dir = DATA / "utility" / task_id
            task_dir.mkdir(parents=True)
            port = readiness[tenant]["port"]
            report = task_dir / "pip-report.json"
            with tempfile.TemporaryDirectory(prefix="p3-package-") as directory:
                target = Path(directory) / "installed"
                args = [sys.executable, "-m", "pip", "install", "--index-url",
                        f"http://127.0.0.1:{port}/simple/", "--no-cache-dir",
                        "--ignore-installed", "--disable-pip-version-check", "--only-binary=:all:",
                        "--no-compile", "--target", str(target), "--report", str(report), requirement]
                started = time.monotonic()
                result = subprocess.run(args, capture_output=True, text=True, env=environment, timeout=120)
                (task_dir / "pip.txt").write_text(result.stdout + result.stderr)
                function = None
                admitted_urls, distributions, downloaded_bytes = True, [], 0
                if report.exists():
                    installed = json.loads(report.read_text())["install"]
                    for entry in installed:
                        url = urlsplit(entry["download_info"]["url"])
                        admitted_urls &= url.scheme == "http" and url.hostname == "127.0.0.1" and url.port == port
                        distributions.append({"name": entry["metadata"]["name"], "version": entry["metadata"]["version"]})
                if result.returncode == 0:
                    module, check = CHECKS[name]
                    script = ("import sys,json,pathlib,importlib,importlib.metadata\n"
                              "target=pathlib.Path(sys.argv[1]).resolve()\n"
                              "sys.path.insert(0,str(target))\n"
                              f"import {module}\n"
                              f"assert target in pathlib.Path({module}.__file__).resolve().parents\n"
                              f"assert importlib.metadata.version({name!r}) == {version!r}\n"
                              f"{check}\n"
                              f"print(json.dumps({{'function_passed':True,'version':{version!r}}}))\n")
                    function = subprocess.run([sys.executable, "-I", "-c", script, str(target)],
                                              capture_output=True, text=True, env=environment, timeout=30)
                    (task_dir / "function.txt").write_text(function.stdout + function.stderr)
                    downloaded_bytes = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
                outcome = {"task_id": task_id, "tenant": tenant, "requirement": requirement,
                           "pip_exit_code": result.returncode,
                           "function_exit_code": function.returncode if function is not None else None,
                           "all_report_urls_on_assigned_service": bool(admitted_urls),
                           "resolved_distributions": distributions, "installed_file_bytes": downloaded_bytes,
                           "elapsed_s": time.monotonic() - started,
                           "passed": result.returncode == 0 and function is not None
                                     and function.returncode == 0 and admitted_urls}
                outcomes.append(outcome)
                with (DATA / "utility-progress.jsonl").open("a") as stream:
                    stream.write(json.dumps(outcome) + "\n")
                print(json.dumps({"task_id": task_id, "passed": outcome["passed"],
                                  "resolved_distributions": len(distributions)}), flush=True)
    finally:
        cleanup = {}
        for tenant, process in services.items():
            memory = subprocess.run(["ps", "-o", "rss=", "-p", str(process.pid)],
                                    capture_output=True, text=True)
            rss_after[tenant] = int(memory.stdout.strip()) if memory.returncode == 0 else None
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            port = readiness[tenant]["port"]
            cleanup[tenant] = {"process_pid": process.pid, "exit_code": process.returncode,
                               "port": port, "socket_closed": closed(port)}
        after = {entry["path"]: sha(DATA / entry["path"]) for entry in manifest["entries"]}
        results = {"utc_ns": time.time_ns(), "python": sys.version, "tasks": outcomes,
                   "scheduled": 28, "completed": len(outcomes), "passed": sum(row["passed"] for row in outcomes),
                   "service_readiness": readiness, "content_unchanged": before == after,
                   "manifest_unchanged": sha(manifest_path) == frozen["manifest_sha256"],
                   "service_rss_kib_before": rss_before, "service_rss_kib_after": rss_after,
                   "cleanup": cleanup, "scope": "Local functional utility, not sandbox or timing-isolation validation."}
        output.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
