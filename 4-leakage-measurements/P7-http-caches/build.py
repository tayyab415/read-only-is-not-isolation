"""Bounded local-image build on the existing, evidence-gated labhost."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess

import service

HERE = Path(__file__).resolve().parent
BUILD_LABEL = "org.research.p7.build=nginx-1.30.6-squid-7.8-v1"


def builder_cleanup():
    """Remove only containers carrying this owned build's explicit label."""
    names = service.old.run_ok(["docker", "ps", "-a", "--filter", "label=" + BUILD_LABEL,
                                "--format", "{{.Names}}"]).splitlines()
    if any(name.startswith(("p7-g1-", "p7-g2-")) for name in names):
        raise RuntimeError("An owned cache experiment is active during a build cleanup")
    for name in names:
        service.old.run(["docker", "rm", "-f", name])
    left = service.old.run_ok(["docker", "ps", "-a", "--filter", "label=" + BUILD_LABEL,
                               "--format", "{{.Names}}"]).splitlines()
    return {"owned_build_label": BUILD_LABEL, "removed_containers": names,
            "containers_readback": left, "absence_proved": not left}


def main():
    with (service.P2 / "collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        service.check_sources()
        service.p2.check_gate()
        existing = service.old.run_ok(["docker", "ps", "-a", "--filter", "label=" + BUILD_LABEL,
                                      "--format", "{{.Names}}"]).splitlines()
        if existing:
            raise RuntimeError("An earlier owned cache/build container remains; reconcile it before building")
        if service.old.free_gib() < 4 or shutil.disk_usage(HERE).free < 8 * 1024 ** 3:
            raise RuntimeError("Insufficient build headroom")
        folder = HERE / "build/g2"
        if folder.exists():
            raise RuntimeError("Preserve any earlier build evidence before a versioned retry")
        folder.mkdir(parents=True)
        dockerfile_hash = service.sha(HERE / "Dockerfile")
        source_hash = service.sha(HERE / "SOURCES-FROZEN.json")
        command = ["docker", "build", "--force-rm", "--memory", "2g", "--memory-swap", "2g",
                   "--cpu-period", "100000", "--cpu-quota", "50000",
                   "--label", "org.research.source-freeze.sha256=" + source_hash,
                   "--tag", service.IMAGE, "."]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("AWS_", "BEDROCK_"))}
        environment["DOCKER_BUILDKIT"] = "0"
        record = {"started_utc": datetime.now(timezone.utc).isoformat(), "command": command,
                  "source_freeze_sha256": source_hash, "dockerfile_sha256": dockerfile_hash,
                  "maximum_wall_seconds": 3600, "memory_limit_bytes": 2147483648,
                  "cpu_limit": .5, "complete": False}
        try:
            with (folder / "build.log").open("w") as stream:
                result = subprocess.run(command, cwd=HERE, env=environment, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=3600)
            record["exit_code"] = result.returncode
            if result.returncode:
                raise RuntimeError("Owned cache image build failed")
            record["readback"] = service.build_check(require_build_record=False)
            record["complete"] = True
        except Exception as error:
            record["failure_type"] = type(error).__name__
            raise
        finally:
            try:
                record["builder_cleanup"] = builder_cleanup()
                if not record["builder_cleanup"]["absence_proved"]:
                    record["complete"] = False
            except Exception as error:
                record.update(complete=False, builder_cleanup_failure_type=type(error).__name__)
            finally:
                record["ended_utc"] = datetime.now(timezone.utc).isoformat()
                (folder / "build-result.json").write_text(json.dumps(record, indent=2) + "\n")
        if not record["complete"]:
            raise RuntimeError("Build completion or cleanup is unproved")
        print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
