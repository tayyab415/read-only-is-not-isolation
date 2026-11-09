#!/usr/bin/env python3
from common import anonymous, permission, req

anonymous(True)
for who in ["anonymous", "a-read", "a-cache", "admin"]:
    req("remote-fresh-" + who, "MKCOL", "/github-remote/zz_A_remote_fresh_" + who, who)
    req("remote-fresh-metadata-" + who, "GET",
        "/api/storage/github-remote-cache/zz_A_remote_fresh_" + who, "admin")
permission(cache=("w",))
req("remote-fresh-deploy-only", "MKCOL", "/github-remote/zz_A_remote_fresh_deploy_only",
    "a-cache")
permission()
anonymous(False)
req("remote-fresh-anonymous-disabled", "MKCOL",
    "/github-remote/zz_A_remote_fresh_anonymous_disabled", "anonymous")
req("remote-final-health", "GET", "/api/system/ping")
print("REMOTE FRESH DONE", flush=True)
