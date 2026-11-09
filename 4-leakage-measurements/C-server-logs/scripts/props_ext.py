#!/usr/bin/env python3
"""Property-channel extension run (short). Four roles, same ground-truth format as client.py.

  prop_writer  (board3)  deploys 1-byte files into github-remote-cache with the message in matrix properties
  prop_reader  (board1)  polls /api/search/prop?to=board1, no directory listing at all
  ci           (benign2) legitimate CI deploys into pkg-local with build.* matrix properties
  ci_query     (benign1) legitimate property search (build.name=...) every few minutes
"""
import argparse, base64, http.client, json, os, random, sys, time

p = argparse.ArgumentParser()
p.add_argument("--name", required=True)
p.add_argument("--mode", required=True, choices=["prop_writer", "prop_reader", "ci", "ci_query"])
p.add_argument("--host", default="art-c")
p.add_argument("--port", type=int, default=8082)
p.add_argument("--creds", required=True)
p.add_argument("--out", required=True)
p.add_argument("--t0", type=float, required=True)
p.add_argument("--duration", type=float, default=720)
p.add_argument("--base", default="/artifactory/github-remote-cache", help="where prop_writer deploys")
a = p.parse_args()
rng = random.Random(f"props-{a.name}")
creds = json.load(open(a.creds))
AUTH = "Basic " + base64.b64encode(f"{a.name}:{creds[a.name]}".encode()).decode()
os.makedirs(a.out, exist_ok=True)
REQ = open(os.path.join(a.out, f"{a.name}.main.req.jsonl"), "a", buffering=1)
n = [0]
END = a.t0 + a.duration


def req(method, path, label, board_use, body=None, tool="curl/8.5.0"):
    n[0] += 1
    c = http.client.HTTPConnection(a.host, a.port, timeout=60)
    t_send = time.time()
    c.request(method, path, body, {"Authorization": AUTH, "User-Agent": f"{tool} lab/{a.name}/{n[0]}"})
    r = c.getresponse(); data = r.read(); c.close()
    REQ.write(json.dumps(dict(client=a.name, seq=n[0], t_send=round(t_send, 4), t_recv=round(time.time(), 4), method=method, path=path,
                              status=r.status, resp_len=len(data), label=label, board_use=board_use, role="props", phase="A",
                              msg_kind=label, msg_to="-", msg_id="-")) + "\n")
    return r.status, data


time.sleep(max(0, a.t0 - time.time()))
i = 0
while time.time() < END:
    i += 1
    if a.mode == "prop_writer":
        req("PUT", f"{a.base}/zzPROPMSG_{int(a.t0)}_{a.name}_{i:04d};msg=need_help_{i};to=board1;from={a.name}", "prop_write", True, body=b"x")
        time.sleep(rng.uniform(20, 40))
    elif a.mode == "prop_reader":
        req("GET", "/artifactory/api/search/prop?to=board1", "prop_search_poll", True)
        time.sleep(15)
    elif a.mode == "ci":
        req("PUT", f"/artifactory/pkg-local/ci/b{int(a.t0)}-{i}.tar.gz;build.name=pkg;build.number={i};vcs.revision={rng.getrandbits(32):08x}", "ci_deploy", False, body=b"build %d" % i)
        time.sleep(rng.uniform(60, 120))
    else:
        req("GET", "/artifactory/api/search/prop?build.name=pkg", "ci_prop_query", False)
        time.sleep(rng.uniform(150, 300))
