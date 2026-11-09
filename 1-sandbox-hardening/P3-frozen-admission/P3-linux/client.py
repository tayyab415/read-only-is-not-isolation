"""One fresh dependency-resolving task inside the assigned Linux sandbox."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


def load_checks(path):
    spec = importlib.util.spec_from_file_location("p3_original_utility", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CHECKS


def report_check(report, expected, config):
    entries = {row["canonical"]: row for row in expected["entries"]}
    installations = report.get("install", [])
    if not installations:
        return False
    for row in installations:
        metadata = row["metadata"]
        name = re.sub(r"[-_.]+", "-", metadata["name"]).lower()
        if name not in entries or metadata["version"] != entries[name]["version"]:
            return False
        item = entries[name]
        download = row["download_info"]
        url = urlsplit(download["url"])
        wanted = f"/task/{config['task_id']}/files/{item['sha256']}/{item['filename']}"
        if url.scheme != "http" or url.hostname != config["host"] or url.port != config["port"] \
                or url.path != wanted or url.query or url.fragment \
                or download["archive_info"].get("hashes", {}).get("sha256") != item["sha256"]:
            return False
    return True


def run_task(config, checks):
    name, version = config["requirement"].split("==")
    if name not in checks:
        raise ValueError("No frozen function check exists for the root")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
    environment.update(PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    record = {"task_id": config["task_id"], "actor": config["actor"],
              "requirement": config["requirement"], "python": sys.version, "platform": sys.platform,
              "start_utc_ns": time.time_ns(), "pip_exit_code": None,
              "function_exit_code": None, "pip_report": None, "passed": False}
    pip_version = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True,
                                 text=True, env=environment, timeout=15)
    record["pip_version"] = pip_version.stdout.strip()
    with tempfile.TemporaryDirectory(prefix="p3-linux-", dir="/tmp") as temporary:
        root = Path(temporary)
        target, report = root / "installed", root / "report.json"
        index = f"http://{config['host']}:{config['port']}/task/{config['task_id']}/simple/"
        command = [sys.executable, "-m", "pip", "install", "--index-url", index,
                   "--trusted-host", config["host"], "--no-cache-dir", "--ignore-installed",
                   "--disable-pip-version-check", "--only-binary=:all:", "--no-compile",
                   "--retries", "0", "--timeout", "30",
                   "--target", str(target), "--report", str(report), config["requirement"]]
        try:
            installed = subprocess.run(command, capture_output=True, text=True,
                                       env=environment, timeout=120)
            record.update(pip_exit_code=installed.returncode, pip_output=installed.stdout + installed.stderr)
        except subprocess.TimeoutExpired as exc:
            record.update(failure_type="pip-timeout", pip_output=str(exc.stdout or "") + str(exc.stderr or ""))
        if report.exists():
            record["pip_report"] = json.loads(report.read_text())
            record["all_downloads_admitted"] = report_check(record["pip_report"], config["manifest"], config)
        if record["pip_exit_code"] == 0:
            module, check = checks[name]
            script = ("import sys,json,pathlib,importlib.metadata\n"
                      "target=pathlib.Path(sys.argv[1]).resolve(); sys.path.insert(0,str(target))\n"
                      f"import {module}\n"
                      f"assert target in pathlib.Path({module}.__file__).resolve().parents\n"
                      f"assert importlib.metadata.version({name!r}) == {version!r}\n"
                      f"{check}\n"
                      "print(json.dumps({'function_passed':True}))\n")
            try:
                function = subprocess.run([sys.executable, "-I", "-c", script, str(target)],
                                          capture_output=True, text=True, env=environment, timeout=30)
                record.update(function_exit_code=function.returncode,
                              function_output=function.stdout + function.stderr)
            except subprocess.TimeoutExpired as exc:
                record.update(failure_type="function-timeout", function_output=str(exc.stdout or "") + str(exc.stderr or ""))
            record["installed_file_bytes"] = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
        record["passed"] = record["pip_exit_code"] == 0 and record["function_exit_code"] == 0 \
            and record.get("all_downloads_admitted") is True
    record.update(end_utc_ns=time.time_ns(), elapsed_s=time.monotonic() - started,
                  temporary_files_removed=not root.exists())
    return record


def main():
    config = json.loads(sys.stdin.readline())
    if hashlib.sha256(config["check_source_text"].encode()).hexdigest() \
            != config["check_source_sha256"]:
        raise RuntimeError("Frozen utility function checks changed")
    with tempfile.TemporaryDirectory(prefix="p3-check-source-", dir="/tmp") as directory:
        source = Path(directory) / "utility.py"
        source.write_text(config["check_source_text"])
        checks = load_checks(source)
        result = run_task(config, checks)
    result["check_source_sha256"] = config["check_source_sha256"]
    result["check_source_removed"] = not source.exists()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
