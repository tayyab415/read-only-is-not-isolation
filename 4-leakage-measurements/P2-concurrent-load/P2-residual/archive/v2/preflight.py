"""Owned-route TCP positive controls before any P2 trial."""
import json
from pathlib import Path
import select
import subprocess

LISTENER = r'''
import json, socket, sys, threading
stop = threading.Event()
threading.Thread(target=lambda: (sys.stdin.readline(), stop.set()), daemon=True).start()
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", 18888))
sock.listen(8)
sock.settimeout(.25)
print(json.dumps({"ready": True, "port": sock.getsockname()[1]}), flush=True)
try:
    while not stop.is_set():
        try:
            connection, _ = sock.accept()
            connection.close()
        except socket.timeout:
            pass
finally:
    sock.close()
'''
PROBE = r'''
import json, socket, sys
out = []
for job in json.loads(sys.stdin.readline()):
    connected, error = False, None
    try:
        connection = socket.create_connection((job["host"], job["port"]), 1)
        connection.close()
        connected = True
    except OSError as exc:
        error = type(exc).__name__
    out.append({**job, "connected": connected, "error": error})
print(json.dumps(out))
'''


def check_results(records):
    if not records or any(row["connected"] != row["expected_connected"] for row in records):
        raise RuntimeError("A network route differs from its positive/negative control")
    return {"passed": True, "checks": len(records), "records": records,
            "scope": "Specified live TCP listeners and routes, not every possible bypass."}


def network_checks(old, run_id, evidence_path=None):
    state = old.load_state(run_id)
    peer = state["containers"]["bob"]
    listener = subprocess.Popen(["docker", "exec", "-i", peer, "python3", "-u", "-c", LISTENER],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
    records = []
    output = {"passed": False, "checks": 0, "records": records, "listener_stopped": False}
    try:
        ready, _, _ = select.select([listener.stdout], [], [], 5)
        if not ready or not json.loads(listener.stdout.readline()).get("ready"):
            raise RuntimeError("The peer-listener positive control failed")
        actor_ips = {actor: old.ip_address(state["containers"][actor], state["networks"][actor])
                     for actor in ("alice", "bob")}
        jobs = {
            "bob": [{"host": actor_ips["bob"], "port": 18888, "expected_connected": True,
                     "name": "live peer listener on its own network"}],
            "alice": [{"host": actor_ips["bob"], "port": 18888, "expected_connected": False,
                       "name": "same live listener from the other network"}]}
        if state.get("kind") == "immutable":
            for actor in ("alice", "bob"):
                other = "bob" if actor == "alice" else "alice"
                own_ip = old.ip_address(state["containers"][f"mirror_{actor}"], state["networks"][actor])
                other_ip = old.ip_address(state["containers"][f"mirror_{other}"], state["networks"][other])
                jobs[actor].extend([
                    {"host": own_ip, "port": 8000, "expected_connected": True, "name": "assigned serving route"},
                    {"host": other_ip, "port": 8000, "expected_connected": False, "name": "peer serving route"}])
        else:
            backend_ip = old.ip_address("art-a", state["networks"]["services"])
            for actor in ("alice", "bob"):
                jobs[actor].extend([
                    {"host": state["containers"]["gateway"], "port": 8000,
                     "expected_connected": True, "name": "assigned gateway route"},
                    {"host": backend_ip, "port": 8082, "expected_connected": False,
                     "name": "direct backend bypass"}])
            jobs["gateway"] = [{"host": backend_ip, "port": 8082,
                                "expected_connected": True, "name": "backend is reachable from the gateway"}]
        for actor, requests in jobs.items():
            # Every target above comes from owned fixture/container readbacks.
            result = old.run(["docker", "exec", "-i", state["containers"][actor],
                              "python3", "-c", PROBE], input=json.dumps(requests), timeout=12)
            if result.returncode:
                raise RuntimeError("A controlled network probe failed")
            response = json.loads(result.stdout)
            if len(response) != len(requests) or any(
                    any(row[key] != job[key] for key in job) for row, job in zip(response, requests)):
                raise RuntimeError("Probe results differ from their owned targets")
            records.extend({**row, "actor": actor} for row in response)
        output.update(check_results(records))
        return output
    except Exception as error:
        output["failure_type"] = type(error).__name__
        raise
    finally:
        try:
            listener.stdin.write("stop\n")
            listener.stdin.flush()
        except BrokenPipeError:
            pass
        listener.stdin.close()
        try:
            listener.wait(timeout=3)
            output["listener_exit_code"] = listener.returncode
            output["listener_stopped"] = listener.returncode == 0
            if listener.returncode:
                output["passed"] = False
                raise RuntimeError("Peer-listener stop failed")
        except subprocess.TimeoutExpired:
            listener.kill()
            listener.wait(timeout=3)
            output.update(passed=False, listener_exit_code=listener.returncode)
            raise RuntimeError("Peer-listener stop was not proved")
        finally:
            listener.stdout.close()
            listener.stderr.close()
            if evidence_path is not None:
                path = Path(evidence_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(output, indent=2))
