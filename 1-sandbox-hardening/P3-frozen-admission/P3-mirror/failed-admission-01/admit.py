#!/usr/bin/env python3
"""Trusted, one-time admission of publisher-verified universal wheels."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
WHEELS = DATA / "wheels"
MANIFEST = DATA / "manifest.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    if MANIFEST.exists():
        raise SystemExit("Admission manifest exists. Keep it; create a new epoch for changes.")
    WHEELS.mkdir(parents=True, exist_ok=True)
    started = time.time_ns()
    args = [sys.executable, "-m", "pip", "--isolated", "download",
            "--index-url", "https://pypi.org/simple", "--only-binary=:all:",
            "--platform", "any", "--python-version", "3.11", "--implementation", "py", "--abi", "none",
            "--disable-pip-version-check", "--no-cache-dir", "--dest", str(WHEELS),
            "-r", str(HERE / "requirements.txt")]
    result = subprocess.run(args, capture_output=True, text=True, timeout=600)
    (DATA / "admission-pip.txt").write_text(result.stdout + result.stderr)
    environment = {"python": sys.version, "executable": sys.executable,
                   "pip": subprocess.run([sys.executable, "-m", "pip", "--version"],
                                         capture_output=True, text=True).stdout.strip(),
                   "command": args, "exit_code": result.returncode,
                   "start_utc_ns": started, "end_utc_ns": time.time_ns()}
    (DATA / "admission-environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    if result.returncode:
        raise SystemExit("Admission resolver failed. See the retained log.")
    entries = []
    for wheel in sorted(WHEELS.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                raise RuntimeError("Wheel metadata is ambiguous")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        name, version = metadata["Name"], metadata["Version"]
        url = f"https://pypi.org/pypi/{canonical(name)}/{version}/json"
        request = urllib.request.Request(url, headers={"User-Agent": "owned-package-mirror-research/1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        publisher = json.loads(raw)
        files = [item for item in publisher["urls"] if item["filename"] == wheel.name]
        digest = sha(wheel)
        if len(files) != 1 or files[0]["digests"]["sha256"] != digest:
            raise RuntimeError(f"Publisher hash mismatch: {wheel.name}")
        if not files[0]["url"].startswith("https://files.pythonhosted.org/"):
            raise RuntimeError("Unexpected publisher file host")
        captured = DATA / "publisher" / f"{canonical(name)}-{version}.json"
        captured.parent.mkdir(parents=True, exist_ok=True)
        captured.write_bytes(raw)
        entries.append({"name": name, "canonical": canonical(name), "version": version,
                        "filename": wheel.name, "sha256": digest, "bytes": wheel.stat().st_size,
                        "requires_dist": metadata.get_all("Requires-Dist", []),
                        "requires_python": metadata["Requires-Python"],
                        "publisher_json_url": url, "publisher_json_sha256": hashlib.sha256(raw).hexdigest(),
                        "publisher_file_url": files[0]["url"],
                        "path": str(wheel.relative_to(DATA))})
    if not entries:
        raise RuntimeError("No wheels admitted")
    manifest = {"schema": 1, "admitted_utc_ns": time.time_ns(),
                "requirements_sha256": sha(HERE / "requirements.txt"),
                "protocol_sha256": sha(HERE / "PROTOCOL.md"),
                "admission_source_sha256": sha(Path(__file__)),
                "entries": entries, "total_bytes": sum(item["bytes"] for item in entries)}
    with MANIFEST.open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    for wheel in WHEELS.glob("*.whl"):
        os.chmod(wheel, 0o444)
    print(json.dumps({"admitted_distributions": len(entries), "wheel_bytes": manifest["total_bytes"],
                      "manifest_sha256": sha(MANIFEST)}, indent=2))


if __name__ == "__main__":
    main()
