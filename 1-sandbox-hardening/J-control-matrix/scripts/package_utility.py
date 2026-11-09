#!/usr/bin/env python3
"""Install a pinned six wheel from the owned mirror under each defense."""
import base64
import copy
import hashlib
import json
import os
import subprocess
import time
from campaign import ROOT, STATE, ARMS, Requests, append, redact, save

CLIENT = r"""
import base64, hashlib, http.client, json, pathlib, subprocess, sys, tempfile, time
d=json.load(sys.stdin)
headers={"Authorization":"Basic "+base64.b64encode((d["username"]+":"+d["password"]).encode()).decode(),
         "User-Agent":d["marker"]}
conn=http.client.HTTPConnection("j-gateway",8000,timeout=35)
t0,m0=time.time_ns(),time.monotonic_ns()
conn.request("GET","/artifactory"+d["path"],headers=headers)
response=conn.getresponse();body=response.read()
result={"status":response.status,"headers":dict(response.getheaders()),
        "start_utc_ns":t0,"end_utc_ns":time.time_ns(),"duration_ns":time.monotonic_ns()-m0,
        "body_b64":base64.b64encode(body).decode()}
conn.close()
if response.status==200 and hashlib.sha256(body).hexdigest()==d["expected_sha256"]:
 directory=pathlib.Path(tempfile.mkdtemp(prefix="j-package-"))
 wheel=directory/"six-1.16.0-py2.py3-none-any.whl";wheel.write_bytes(body)
 target=directory/"installed"
 install=subprocess.run([sys.executable,"-m","pip","install","--no-index","--no-deps",
     "--no-cache-dir","--disable-pip-version-check","--target",str(target),str(wheel)],
     capture_output=True,text=True,timeout=30)
 result["pip_exit_code"]=install.returncode
 result["pip_output"]=install.stdout+install.stderr
 if install.returncode==0:
  sys.path.insert(0,str(target));import six
  result["installed_version"]=six.__version__
  result["package_function_passed"]=six.ensure_str(b"works")=="works"
print(json.dumps(result))
"""


def main():
    state = json.loads(STATE.read_text())
    q = Requests(state)
    if not (q.out / "identity-finished.json").exists():
        raise RuntimeError("Complete identity experiment first")
    if (q.out / "package-preregister.json").exists():
        raise RuntimeError("Package utility already started; inspect before resuming")
    source = json.loads((ROOT / "package/source.json").read_text())
    digest = source["measured_sha256"]
    filename = source["file"]["filename"]
    save(q.out / "package-preregister.json", {
        "created_utc_ns": time.time_ns(), "package": "six", "version": "1.16.0",
        "filename": filename, "expected_sha256": digest, "arms": list(ARMS),
        "clients_per_arm": 2, "task": "pip install local wheel then check version and ensure_str",
        "network": "No pip index or dependency resolution; the package download uses the isolated mirror.",
    })
    save(q.out / "package-source.json", source)
    save(q.out / "package-start.json", {"utc_ns": time.time_ns(), "pid": os.getpid()})
    relative = "/packages/" + filename
    for arm in ARMS:
        pair = state["repos"][arm]
        offline_config = None
        if arm == "offline":
            repo = pair["sender"]
            _, body = q.required("package-offline-config", "GET", "/ui/admin/repositories/remote/" + repo)
            offline_config = json.loads(body)
            online = copy.deepcopy(offline_config)
            online["basic"]["offline"] = False
            q.required("package-online-for-prewarm", "PUT", "/ui/admin/repositories", body=online)
        try:
            for repo in sorted(set(pair.values())):
                record, _ = q.required("package-prewarm-" + repo, "GET", "/" + repo + relative,
                                        "prefetch", direct=True)
                if record["body_sha256"] != digest:
                    raise RuntimeError("Prefetched package checksum mismatch")
        finally:
            if offline_config:
                q.required("package-restore-offline", "PUT", "/ui/admin/repositories", body=offline_config)
        for role in ("sender", "receiver"):
            label = f"package-{arm}-{role}"
            marker = f'j-infra/{state["run"]}/{label}'
            path = "/" + pair[role] + relative
            proc = subprocess.run(
                ["docker", "exec", "-i", "j-" + role, "python3", "-c", CLIENT],
                input=json.dumps({
                    "username": state["users"][role], "password": q.creds[role],
                    "marker": marker, "path": path, "expected_sha256": digest,
                }), text=True, capture_output=True, timeout=75,
            )
            if proc.returncode:
                raise RuntimeError(redact(proc.stderr[-500:], q.creds))
            data = json.loads(proc.stdout)
            payload = base64.b64decode(data.pop("body_b64"))
            body_file = "bodies/" + label + ".whl"
            (q.out / body_file).write_bytes(payload)
            record = {
                "label": label, "run": state["run"], "identity": state["users"][role], "role": role,
                "transport": "j-" + role, "method": "GET", "path": path, "marker": marker,
                **{key: data[key] for key in ("status", "headers", "start_utc_ns", "end_utc_ns", "duration_ns")},
                "body_file": body_file, "body_sha256": hashlib.sha256(payload).hexdigest(),
                "body_bytes": len(payload), "request_body": None, "request_headers_without_auth": {},
            }
            append(q.out / "requests.jsonl", record)
            outcome = {"arm": arm, "role": role, "label": label, "download_status": data["status"],
                       "download_sha256": record["body_sha256"], **{
                           key: data.get(key) for key in ("pip_exit_code", "pip_output", "installed_version",
                                                        "package_function_passed")}}
            append(q.out / "package-trials.jsonl", outcome)
            if data["status"] != 200 or record["body_sha256"] != digest or data.get("pip_exit_code") != 0 or not data.get("package_function_passed"):
                raise RuntimeError("Package utility failed; retain observation")
        print("Package installs passed:", arm, flush=True)
    save(q.out / "package-finished.json", {"utc_ns": time.time_ns(), "status": "complete"})
    print("PACKAGE UTILITY COMPLETE", flush=True)


if __name__ == "__main__":
    main()
