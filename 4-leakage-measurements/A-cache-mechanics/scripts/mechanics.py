#!/usr/bin/env python3
"""Bounded tests on A's private instance; bulk MKCOL never contacts GitHub."""
import json
from urllib.parse import quote
from common import LAB, anonymous, ok, permission, req

USERS = ["anonymous", "a-read", "a-cache", "admin"]
FILE = "github/gitignore/raw/refs/heads/main/Python.gitignore"
CACHE = "/github-remote-cache/"
REMOTE = "/github-remote/"
DAV = {"Depth": "1", "Content-Type": "application/xml"}
DAV_BODY = '<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:allprop/></D:propfind>'

def listings(prefix, path, users=USERS):
    for who in users:
        req(prefix + "-html-" + who, "GET", "/" + path.rstrip("/") + "/", who)
        req(prefix + "-storage-" + who, "GET", "/api/storage/" + path, who)
        req(prefix + "-list-" + who, "GET", "/api/storage/" + path + "?list&deep=0&listFolders=1", who)
        req(prefix + "-dav-" + who, "PROPFIND", "/" + path.rstrip("/") + "/", who,
            data=DAV_BODY, headers=DAV)
        req(prefix + "-repos-" + who, "GET", "/api/repositories", who)

req("read-uncached-file", "GET", REMOTE + FILE, "a-read")
ok(req("cache-user-fetch-file", "GET", REMOTE + FILE, "a-cache"))
for who in USERS:
    req("cached-file-" + who, "GET", REMOTE + FILE, who)
listings("anon-off", "github-remote-cache/github/gitignore/raw/refs/heads/main")
anonymous(True)
listings("anon-on", "github-remote-cache/github/gitignore/raw/refs/heads/main")
listings("remote-parent", "github-remote/github/gitignore/raw/refs/heads/main",
         ["a-read", "a-cache", "admin"])

for who in USERS:
    for repo in ("github-remote-cache", "github-remote"):
        req("mkcol-" + repo + "-" + who, "MKCOL",
            "/" + repo + "/zz_A_from_" + who, who)
req("repeat-mkcol", "MKCOL", CACHE + "zz_A_from_a-cache", "a-cache")

permission(cache=("w",))
req("deploy-only-mkcol", "MKCOL", CACHE + "zz_A_deploy_only", "a-cache")
req("deploy-only-read", "GET", "/api/storage/github-remote-cache/zz_A_deploy_only", "a-cache")
req("deploy-only-remote-mkcol", "MKCOL", REMOTE + "zz_A_deploy_only_remote", "a-cache")
permission()

permission(anon=("r", "w"))
req("anonymous-deploy-mkcol", "MKCOL", CACHE + "zz_A_anonymous_deploy", "anonymous")
permission()

ok(req("parent-by-cache-user", "MKCOL", CACHE + "zz_A_inbox", "a-cache"))
for who in USERS + ["a-cache2"]:
    req("nested-" + who, "MKCOL", CACHE + "zz_A_inbox/zz_note_from_" + who, who)
for who in USERS + ["a-cache2"]:
    req("metadata-nested-" + who, "GET",
        "/api/storage/github-remote-cache/zz_A_inbox/zz_note_from_a-cache2", who)
for who in USERS[:-1] + ["a-cache2"]:
    req("delete-other-" + who, "DELETE", CACHE + "zz_A_from_admin", who)
req("admin-delete-other", "DELETE", CACHE + "zz_A_from_a-cache", "admin")
req("delete-own-no-delete", "DELETE", CACHE + "zz_A_inbox/zz_note_from_a-cache2", "a-cache2")

for who in USERS:
    req("set-property-" + who, "PUT",
        "/api/storage/github-remote-cache/" + FILE
        + "?properties=lab.note=from_" + who + "&recursive=0", who)
    req("read-property-" + who, "GET",
        "/api/storage/github-remote-cache/" + FILE + "?properties", who)
permission(cache=("r", "w", "n"))
req("annotate-property", "PUT",
    "/api/storage/github-remote-cache/" + FILE + "?properties=lab.note=annotator&recursive=0",
    "a-cache")
req("annotate-folder-property", "PUT",
    "/api/storage/github-remote-cache/zz_A_inbox?properties=lab.note=inbox&recursive=0",
    "a-cache")
for who in USERS:
    req("annotated-read-" + who, "GET",
        "/api/storage/github-remote-cache/" + FILE + "?properties", who)
permission()

ok(req("sort-parent", "MKCOL", CACHE + "zz_A_sort", "a-cache"))
for name in ["zz_last", "a_first", "M_Mixed", "aa_second", "z_middle", "Z_Upper"]:
    req("sort-create-" + name, "MKCOL", CACHE + "zz_A_sort/" + name, "a-cache")
listings("sort", "github-remote-cache/zz_A_sort", ["a-read", "admin"])
req("sort-html-desc-query", "GET", CACHE + "zz_A_sort/?C=N;O=D", "a-read")
req("sort-filelist-desc", "GET",
    "/api/storage/github-remote-cache/zz_A_sort?list&listFolders=1&sort=desc", "admin")

ok(req("name-parent", "MKCOL", CACHE + "zz_A_names", "a-cache"))
names = [(str(n), "n" * n) for n in [254, 255, 256, 257, 512, 1024]]
names += [(label, name) for label, name in [
    ("spaces", "message with spaces"), ("unicode", "café_测试"),
    ("punctuation", "note_[a]{b}(c)"), ("reserved", "note_?#&=+%"),
    ("colon", "note:colon"), ("asterisk", "note*star"), ("backslash", "note\\backslash"),
    ("dot", "."), ("dotdot", "..")]]
for label, name in names:
    req("name-" + label, "MKCOL", CACHE + "zz_A_names/" + quote(name, safe=""), "a-cache")
listings("names", "github-remote-cache/zz_A_names", ["admin"])

for index in range(3):
    path = "zz_A_missing_" + str(index) + "/invented_discussion_name_" + str(index)
    req("miss-" + str(index), "GET", REMOTE + path, "a-cache")
    req("miss-repeat-" + str(index), "GET", REMOTE + path, "a-cache2")
    req("miss-parent-" + str(index), "GET",
        "/api/storage/github-remote-cache/zz_A_missing_" + str(index), "admin")
req("missing-root-list", "GET", "/api/storage/github-remote-cache", "admin")

# Test controls with the same identity and save the effective configuration.
permission(exclude="zz_A_denied/**")
req("exclude-parent", "MKCOL", CACHE + "zz_A_denied", "a-cache")
req("exclude-child", "MKCOL", CACHE + "zz_A_denied/note", "a-cache")
permission()
config = ok(req("config-before-controls", "GET", "/ui/admin/repositories/remote/github-remote")).json()
import copy
changed = copy.deepcopy(config)
changed["advanced"]["storeArtifactsLocally"] = False
ok(req("disable-store-artifacts", "PUT", "/ui/admin/repositories", data=changed))
req("no-store-mkcol", "MKCOL", CACHE + "zz_A_no_store", "a-cache")
req("no-store-remote-mkcol", "MKCOL", REMOTE + "zz_A_no_store_remote", "a-cache")
req("no-store-fetch", "GET", REMOTE + "github/gitignore/raw/refs/heads/main/Node.gitignore", "a-cache")
req("no-store-fetched-metadata", "GET",
    "/api/storage/github-remote-cache/github/gitignore/raw/refs/heads/main/Node.gitignore",
    "admin")
ok(req("restore-store-artifacts", "PUT", "/ui/admin/repositories", data=config))
changed = copy.deepcopy(config)
changed["basic"]["offline"] = True
ok(req("set-offline", "PUT", "/ui/admin/repositories", data=changed))
req("offline-mkcol", "MKCOL", CACHE + "zz_A_offline", "a-cache")
ok(req("restore-online", "PUT", "/ui/admin/repositories", data=config))
anonymous(False)
req("final-cache-metadata", "GET", "/api/storage/github-remote-cache", "admin")
print("MECHANICS DONE", flush=True)
