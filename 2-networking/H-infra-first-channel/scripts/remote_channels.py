#!/usr/bin/env python3
"""Owned remote repo: local redirect and cache-miss signalling controls."""
import json
import secrets
import time
from probe import CREDS, OUT, PRIVATE, ROUTES, RUN, req, require

REPO = RUN + "-remote"
R1, R2, FETCH = "h-remote1", "h-remote2", "h-fetch"


def main():
    for user in (R1, R2, FETCH):
        private = json.loads(PRIVATE.read_text())
        if user not in private:
            private[user] = secrets.token_urlsafe(24)
            PRIVATE.write_text(json.dumps(private))
        CREDS[user] = private[user]
        status, _ = req("find-" + user, "GET", "/ui/users/" + user)
        if status != 200:
            require(req("create-" + user, "POST", "/ui/users", body={
                "name": user, "email": user + "@example.invalid",
                "password": CREDS[user], "admin": False, "groups": [],
                "profileUpdatable": False,
            }))
    ROUTES.update({R1: "h-sender", R2: "h-receiver", FETCH: "h-sender"})
    require(req("create-remote", "POST", "/ui/admin/repositories", body={
        "type": "remoteRepoConfig", "general": {"repoKey": REPO},
        "basic": {"url": "http://h-upstream:8000", "includesPattern": "**/*",
                  "layout": "simple-default", "offline": False},
        "advanced": {"storeArtifactsLocally": True},
        "typeSpecific": {"repoType": "Generic", "listRemoteFolderItems": False},
    }))
    require(req("remote-grants", "POST", "/ui/permissiontargets", body={
        "name": RUN + "-remote-perm",
        "repoPermission": {"repoKeys": [REPO], "includePatterns": ["**"],
            "userPermissionActions": [
                {"principal": R1, "actions": ["read"]},
                {"principal": R2, "actions": ["read"]},
                {"principal": FETCH, "actions": ["read", "upload"]},
            ], "groupPermissionActions": []},
    }))
    config = require(req("remote-config", "GET", "/ui/admin/repositories/remote/" + REPO))
    (OUT / "remote-state.json").write_text(json.dumps(
        {"run": RUN, "repo": REPO, "config": json.loads(config)}, indent=2))

    req("redirect-read-only", "GET", f"/{REPO}/redirect/read-only.bin", R1)
    req("redirect-cache", "GET", f"/{REPO}/redirect/cache.bin", FETCH)
    req("redirect-prewarmed-read", "GET", f"/{REPO}/redirect/cache.bin", R2)

    # A fresh path for every paired observation; request counts in upstream logs
    # distinguish cache state from mere client/container latency.
    for i in range(12):
        p = f"/{REPO}/missing/read-pair-{i}.bin"
        req(f"miss-read-{i}-sender", "GET", p, R1)
        req(f"miss-read-{i}-receiver", "GET", p, R2)
    for i in range(4):
        p = f"/{REPO}/missing/cache-pair-{i}.bin"
        req(f"miss-cache-{i}-sender", "GET", p, FETCH)
        req(f"miss-cache-{i}-receiver", "GET", p, R2)
    req("remote-mkcol-read-denied", "MKCOL", f"/{REPO}-cache/check", R1)
    req("remote-put-read-denied", "PUT", f"/{REPO}-cache/check.txt", R1, "fixture")
    print("Local redirect and negative-cache probes finished.", flush=True)


if __name__ == "__main__":
    main()
