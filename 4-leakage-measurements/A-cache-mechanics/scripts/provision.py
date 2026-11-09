#!/usr/bin/env python3
import json
import time
from common import BASE, CREDS, LAB, SESSION, anonymous, ok, permission, req

for i in range(90):
    try:
        r = SESSION.get(BASE + "/api/system/ping", timeout=5)
        if r.status_code == 200 and r.text == "OK":
            break
    except Exception:
        pass
    if i % 6 == 0:
        print("Waiting for art-a", i, flush=True)
    time.sleep(5)
else:
    raise RuntimeError("Artifactory did not become ready")

# The first admin password change used PUT /ui/users/admin with default Basic
# credentials. It succeeded. Repeat with the new credentials for the transcript.
req("legacy-user-api-oss-gate", "PUT", "/api/security/users/admin",
    data={"name": "admin", "password": CREDS["admin"], "admin": True})
ok(req("secure-admin-ui", "PUT", "/ui/users/admin",
    data={"name": "admin", "email": "a-admin@example.invalid",
          "password": CREDS["admin"], "retypedPassword": CREDS["admin"],
          "admin": True, "profileUpdatable": True}))
ok(req("secured-admin-version", "GET", "/api/system/version"))
print("Administrator password changed", flush=True)

req("default-permissions-legacy", "GET", "/api/security/permissions")
req("default-permission-anything", "GET", "/ui/permissiontargets/Anything")
req("default-permission-remote", "GET", "/ui/permissiontargets/Any%20Remote")
for name in ("a-read", "a-cache", "a-cache2"):
    existing = req("existing-" + name, "GET", "/ui/users/" + name)
    if existing.status_code == 200:
        continue
    ok(req("create-" + name, "POST", "/ui/users",
        data={"name": name, "email": name + "@example.invalid",
              "password": CREDS[name], "admin": False, "groups": [],
              "profileUpdatable": False}))
req("create-github-remote-legacy", "PUT", "/api/repositories/github-remote",
    data={"key": "github-remote", "rclass": "remote", "packageType": "generic",
          "url": "https://github.com", "storeArtifactsLocally": True,
          "socketTimeoutMillis": 20000, "missedRetrievalCachePeriodSecs": 1800})
existing = req("existing-repository-ui", "GET", "/ui/admin/repositories/remote/github-remote")
if existing.status_code != 200:
    ok(req("create-github-remote-ui", "POST", "/ui/admin/repositories", data={
        "type": "remoteRepoConfig", "general": {"repoKey": "github-remote"},
        "basic": {"url": "https://github.com", "includesPattern": "**/*",
                  "layout": "simple-default", "offline": False},
        "advanced": {"storeArtifactsLocally": True},
        "typeSpecific": {"repoType": "Generic", "listRemoteFolderItems": False}}))
actions = {"name": "a-test", "repoPermission": {
    "repoKeys": ["github-remote"], "includePatterns": ["**"],
    "userPermissionActions": [
        {"principal": "a-read", "actions": ["read"]},
        {"principal": "a-cache", "actions": ["read", "upload"]},
        {"principal": "a-cache2", "actions": ["read", "upload"]},
        {"principal": "anonymous", "actions": ["read"]}],
    "groupPermissionActions": []}}
existing = req("existing-permission-ui", "GET", "/ui/permissiontargets/a-test")
if existing.status_code == 200:
    permission()
else:
    ok(req("create-permission-ui", "POST", "/ui/permissiontargets", data=actions))
anonymous(False)
ok(req("configured-repository", "GET", "/ui/admin/repositories/remote/github-remote"))
ok(req("configured-permission", "GET", "/ui/permissiontargets/a-test"))
(LAB / "READY").write_text(
    "internal_url=http://art-a:8082/artifactory\n"
    "host_url=http://127.0.0.1:18584/artifactory\n"
    "credentials_file=" + str(LAB / "credentials.json") + "\n"
    "docker_network=art-a-net\n")
print("READY written", flush=True)
