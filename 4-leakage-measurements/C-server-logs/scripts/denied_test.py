#!/usr/bin/env python3
"""Read-only user tries MKCOL / PUT on the cache and the local repo. Prints what each returned."""
import base64, http.client, json, os, requests
LAB = os.path.expanduser("~/workspaces/artifactory-lab/c")
B = "http://127.0.0.1:18582/artifactory"
c = json.load(open(f"{LAB}/creds.json"))
s = requests.Session(); s.auth = ("admin", c["admin"])
if s.get(f"{B}/ui/users/ro1").status_code != 200:
    c["ro1"] = "Cl-ro1-readonly-pw1"
    print(s.post(f"{B}/ui/users", json={"name": "ro1", "email": "ro1@example.invalid", "password": c["ro1"], "admin": False, "groups": [], "profileUpdatable": False}).status_code)
    json.dump(c, open(f"{LAB}/creds.json", "w"), indent=1)
    print(s.post(f"{B}/ui/permissiontargets", json={"name": "ro-test", "repoPermission": {"repoKeys": ["github-remote", "pkg-local"], "includePatterns": ["**"], "excludePatterns": [],
          "userPermissionActions": [{"principal": "ro1", "actions": ["read"]}], "groupPermissionActions": []}}).status_code)
import time; time.sleep(3)
A = "Basic " + base64.b64encode(f"ro1:{c['ro1']}".encode()).decode()
for m, p in [("PROPFIND", "/artifactory/github-remote-cache/"), ("MKCOL", "/artifactory/github-remote-cache/zzDENY_1"), ("MKCOL", "/artifactory/github-remote-cache/zzDENY_2/inbox"),
             ("PUT", "/artifactory/pkg-local/denied/x.txt;k=v"), ("MKCOL", "/artifactory/pkg-local/denied")]:
    con = http.client.HTTPConnection("127.0.0.1", 18582)
    con.request(m, p, b"x" if m == "PUT" else None, {"Authorization": A, "User-Agent": "denied-test/1", "Depth": "1"})
    r = con.getresponse(); r.read(); print(m, p, r.status)
