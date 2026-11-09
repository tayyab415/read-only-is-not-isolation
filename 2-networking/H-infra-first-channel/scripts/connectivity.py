#!/usr/bin/env python3
"""Positive and negative controls for H's direct network connections."""
import json
import subprocess
import time
from probe import OUT

PY = """
import json, socket, sys, time
for host,port in json.load(sys.stdin):
    t=time.monotonic()
    try:
        with socket.create_connection((host,port),timeout=0.5):
            result="connected"
    except OSError as e:
        result=type(e).__name__+": "+str(e)
    print(json.dumps({"host":host,"port":port,"result":result,"duration_s":time.monotonic()-t}))
"""


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]


def probe(container, destinations):
    p = subprocess.run(["docker", "exec", "-i", container, "python3", "-c", PY],
                       input=json.dumps(destinations), capture_output=True, text=True, check=True)
    return [json.loads(x) for x in p.stdout.splitlines()]


def main():
    subprocess.run(["docker", "exec", "-d", "h-receiver", "python3", "-m", "http.server", "8765"],
                   check=True)
    receiver = inspect("h-receiver")["NetworkSettings"]["Networks"]["h-net-receiver"]["IPAddress"]
    target = inspect("h-target")["NetworkSettings"]["Networks"]["h-net-services"]["IPAddress"]
    def ready(container, port):
        # Detached process startup is asynchronous. Retain every readiness probe.
        observations = []
        for _ in range(20):
            observations.extend(probe(container, [("127.0.0.1", port)]))
            if observations[-1]["result"] == "connected":
                return observations
            time.sleep(0.1)
        return observations

    results = {
        "receiver_listener_control": ready("h-receiver", 8765),
        "target_listener_control": ready("h-target", 8000),
        "sender": probe("h-sender", [("art-a", 8082), (receiver, 8765), (target, 8000)]),
    }
    (OUT / "connectivity.json").write_text(json.dumps(results, indent=2))
    assert results["receiver_listener_control"][-1]["result"] == "connected"
    assert results["target_listener_control"][-1]["result"] == "connected"
    assert results["sender"][0]["result"] == "connected"
    assert all(x["result"] != "connected" for x in results["sender"][1:])
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
