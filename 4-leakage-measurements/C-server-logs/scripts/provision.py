#!/usr/bin/env python3
"""Provision art-c: admin password, one user per client, permission target.
Runs on the labhost against the host-mapped port. Credentials go to ../creds.json (0600)."""
import json, os, secrets, sys, requests

BASE = "http://127.0.0.1:18582/artifactory"
LAB = os.path.expanduser("~/workspaces/artifactory-lab/c")
CRED = os.path.join(LAB, "creds.json")
CLIENTS = ["benign1", "benign2", "board1", "board2", "board3", "board4", "mixed1", "mixed2"]

creds = json.load(open(CRED)) if os.path.exists(CRED) else {}
admin_pw = creds.get("admin", "password")
s = requests.Session()
s.auth = ("admin", admin_pw)
if "admin" not in creds:
    creds["admin"] = "Adm1n-" + secrets.token_hex(6)
    r = s.put(BASE + "/ui/users/admin", json={
        "name": "admin", "email": "c-admin@example.invalid", "password": creds["admin"],
        "retypedPassword": creds["admin"], "admin": True, "profileUpdatable": True})
    print("admin pw change", r.status_code, r.text[:200])
    r.raise_for_status()
    s.auth = ("admin", creds["admin"])
for c in CLIENTS:
    pw = creds.setdefault(c, "Cl-" + secrets.token_hex(8))
    r = s.get(BASE + "/ui/users/" + c)
    if r.status_code == 200:
        continue
    r = s.post(BASE + "/ui/users", json={"name": c, "email": c + "@example.invalid", "password": pw,
                                           "admin": False, "groups": [], "profileUpdatable": False})
    print("create", c, r.status_code, r.text[:200])
json.dump(creds, open(CRED, "w"), indent=1)
os.chmod(CRED, 0o600)
print("users done")
