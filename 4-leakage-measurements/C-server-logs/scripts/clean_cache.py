#!/usr/bin/env python3
"""Delete every zz* entry in github-remote-cache (admin). Used between runs."""
import json, os, re, requests
LAB = os.path.expanduser("~/workspaces/artifactory-lab/c")
B = "http://127.0.0.1:18582/artifactory"
c = json.load(open(f"{LAB}/creds.json"))
s = requests.Session(); s.auth = ("admin", c["admin"])
r = s.request("PROPFIND", f"{B}/github-remote-cache/", headers={"Depth": "1"})
names = [h.strip("/").split("/")[-1] for h in re.findall(r"<D:href>([^<]*)</D:href>", r.text)]
n = 0
for nm in names:
    if nm.startswith("zz"):
        s.delete(f"{B}/github-remote-cache/{nm}"); n += 1
print("deleted", n)
