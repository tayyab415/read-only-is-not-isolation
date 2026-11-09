#!/usr/bin/env python3
"""Upload fake packages to pkg-local and write clients-creds.json (no admin password)."""
import json, os, random, requests
LAB = os.path.expanduser("~/workspaces/artifactory-lab/c")
B = "http://127.0.0.1:18582/artifactory"
c = json.load(open(f"{LAB}/creds.json"))
s = requests.Session(); s.auth = ("admin", c["admin"])
rng = random.Random(7)
n = 0
for name in ("core", "utils", "net", "crypto", "parser", "log", "cli", "io", "math", "text"):
    for v in ("1.0.0", "1.1.0", "2.0.0", "2.1.0"):
        size = rng.choice([2_000, 8_000, 30_000, 90_000, 150_000])
        r = s.put(f"{B}/pkg-local/pkgs/{name}/{v}/{name}-{v}.tar.gz", data=rng.randbytes(size))
        assert r.status_code == 201, r.text
        n += 1
print("uploaded", n)
json.dump({k: v for k, v in c.items() if k != "admin"}, open(f"{LAB}/clients-creds.json", "w"))
os.chmod(f"{LAB}/clients-creds.json", 0o644)
