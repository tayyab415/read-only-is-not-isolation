#!/usr/bin/env python3
"""Create harness-agent1..N users and one permission target on an Artifactory OSS instance.
OSS gates /api/security/users and /api/v2/security/permissions (HTTP 400 "available only in Artifactory Pro").
The /ui/users and /ui/permissiontargets endpoints that the web UI uses are not gated, so this uses those.

  art-users.py BASE_URL ADMIN_CREDS_JSON ADMIN_USER REPO[,REPO...] [N]

Admin password is read from the creds file and never printed. User passwords are random and are written
to ~/workspaces/artifactory-lab/d/creds/harness-agentN (mode 600). Permissions: Read + Deploy/Cache
("r","w") on the listed repos and their -cache twins. No delete, no annotate (properties), no manage.
"""
import base64, json, os, secrets, sys, urllib.request, urllib.error
base, credfile, admin, repos = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3], sys.argv[4].split(",")
n = int(sys.argv[5]) if len(sys.argv) > 5 else 3
apw = json.load(open(credfile))[admin]
auth = "Basic " + base64.b64encode(f"{admin}:{apw}".encode()).decode()
outdir = os.path.expanduser("~/workspaces/artifactory-lab/d/creds")
os.makedirs(outdir, mode=0o700, exist_ok=True)

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method,
                               headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(r, timeout=30)
        return resp.status, resp.read().decode()[:1500]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]

users = []
for i in range(1, n + 1):
    u = f"harness-agent{i}"
    f = os.path.join(outdir, u)
    if not os.path.exists(f):
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(24))
    pw = open(f).read().strip()
    st, body = call("POST", "/ui/users",
                    {"name": u, "email": f"{u}@harness.invalid", "password": pw, "admin": False,
                     "groups": [], "profileUpdatable": False})
    print("create user", u, st, body.replace(pw, "<pw>")[:120])
    users.append(u)

target = {"name": "harness-install", "repoPermission": {
    "repoKeys": repos, "includePatterns": ["**"],
    "userPermissionActions": [{"principal": u, "actions": ["read", "upload"]} for u in users],
    "groupPermissionActions": []}}
# The UI backend calls Deploy/Cache "upload". Unknown action strings are silently dropped ("deploy" was), so read back.
st, body = call("POST", "/ui/permissiontargets", target)
if st == 409:  # already exists: update in place
    st, body = call("PUT", "/ui/permissiontargets/harness-install", target)
print("permission target harness-install", st, body[:200])
st, body = call("GET", "/ui/permissiontargets/harness-install")
print("stored permission target:", body if st == 200 else st)
