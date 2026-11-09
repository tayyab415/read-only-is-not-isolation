#!/usr/bin/env python3
"""Test credential lending and a trusted network-to-principal binding."""
import base64
import hashlib
import json
import random
import subprocess
import time
from campaign import ROOT, STATE, Requests, CLIENT_CODE, append, redact, save


def request(q, label, container, credential_role, path, password=None):
    marker = f'j-infra/{q.state["run"]}/{label}'
    username = q.state["users"][credential_role]
    private_input = {
        "username": username, "password": password or q.creds[credential_role],
        "marker": marker, "method": "GET", "path": path,
    }
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "python3", "-c", CLIENT_CODE],
        input=json.dumps(private_input), text=True, capture_output=True, timeout=45,
    )
    if proc.returncode:
        raise RuntimeError(redact(proc.stderr[-500:], q.creds))
    data = json.loads(proc.stdout)
    body = base64.b64decode(data.pop("body_b64"))
    body_file = "bodies/" + label + ".txt"
    (q.out / body_file).write_text(redact(body.decode(errors="replace"), q.creds))
    row = {
        "label": label, "run": q.state["run"], "identity": username,
        "role": credential_role, "transport": container, "method": "GET", "path": path,
        "marker": marker, **data, "body_file": body_file,
        "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body),
        "request_body": None, "request_headers_without_auth": {},
    }
    append(q.out / "requests.jsonl", row)
    return row


def main():
    state = json.loads(STATE.read_text())
    q = Requests(state)
    if not (q.out / "followup-finished.json").exists():
        raise RuntimeError("Complete the main and load followup before changing gateway policy")
    plan_path = q.out / "identity-preregister.json"
    if plan_path.exists():
        raise RuntimeError("Identity experiment already started; inspect it")
    order = ["sender"] * 8 + ["receiver"] * 8
    random.Random(2026110803).shuffle(order)
    bindings = {}
    for role in ("sender", "receiver"):
        container = "j-" + role
        data = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
        ip = data["NetworkSettings"]["Networks"]["j-net-" + role]["IPAddress"]
        bindings[ip] = state["users"][role]
    save(plan_path, {"seed": 2026110803, "roles": order, "bindings": bindings,
                     "created_utc_ns": time.time_ns(), "arms": ["account-only", "origin-bound"],
                     "scope": "Copied credentials supplied deliberately by the host; no credential theft.",
                     "network_assumption": "Trusted host and network, clients lack network capabilities."})
    for arm in ("account-only", "origin-bound"):
        if arm == "origin-bound":
            config = json.loads((ROOT / "gateway.json").read_text())
            config["origin_binding"] = bindings
            # Write in place so the read-only file bind mount retains its inode.
            save(ROOT / "gateway.json", config)
            save(q.out / "origin-bound-policy.json", config)
            subprocess.run(["docker", "restart", "j-gateway"], check=True, capture_output=True)
            # Readiness is checked against the real process, without generating lab requests.
            code = "import socket;c=socket.create_connection(('j-gateway',8000),timeout=1);c.close()"
            for _ in range(20):
                ready = subprocess.run(["docker", "exec", "j-sender", "python3", "-c", code],
                                       capture_output=True, timeout=3)
                if ready.returncode == 0:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Gateway did not become ready")
        for i, role in enumerate(order):
            other = "receiver" if role == "sender" else "sender"
            own_repo, peer_repo = state["repos"]["partitioned"][role], state["repos"]["partitioned"][other]
            borrowed = request(q, f"identity-{arm}-{i}-borrowed", "j-" + role, other,
                               f"/{peer_repo}/present/common.bin")
            own = request(q, f"identity-{arm}-{i}-own", "j-" + role, role,
                          f"/{own_repo}/present/common.bin")
            append(q.out / "identity-trials.jsonl", {
                "arm": arm, "index": i, "client_role": role, "borrowed_credential_role": other,
                "borrowed_label": borrowed["label"], "borrowed_status": borrowed["status"],
                "own_label": own["label"], "own_status": own["status"],
                "own_body_sha256": own["body_sha256"],
            })
        for role in ("sender", "receiver"):
            own_repo = state["repos"]["partitioned"][role]
            row = request(q, f"identity-{arm}-{role}-wrong-password", "j-" + role, role,
                          f"/{own_repo}/present/common.bin", password="deliberately-invalid-fixture")
            append(q.out / "identity-negative-controls.jsonl", {
                "arm": arm, "client_role": role, "label": row["label"], "status": row["status"],
            })
        print("Identity arm done:", arm, flush=True)
    save(q.out / "identity-finished.json", {"utc_ns": time.time_ns(), "status": "complete"})
    print("IDENTITY EXPERIMENT COMPLETE", flush=True)


if __name__ == "__main__":
    main()
