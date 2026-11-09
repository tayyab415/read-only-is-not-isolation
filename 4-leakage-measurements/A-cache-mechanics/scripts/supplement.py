#!/usr/bin/env python3
import json
import subprocess
from urllib.parse import quote
from common import BASE, CREDS, LAB, OUT, SESSION, anonymous, ok, permission, req

FILE = "github/gitignore/raw/refs/heads/main/Python.gitignore"
item_query = "?repoKey=github-remote-cache&path=" + quote(FILE, safe="")
USERS = ["anonymous", "a-read", "a-cache", "admin"]
anonymous(True)
for who in USERS:
    req("ui-property-create-" + who, "POST", "/ui/artifactproperties" + item_query,
        who, data={"property": {"name": "lab.ui_note"},
                   "selectedValues": ["from_" + who]})
    req("ui-property-read-" + who, "GET", "/ui/artifactproperties" + item_query, who)
permission(cache=("r", "w", "n"))
req("ui-annotate-property", "POST", "/ui/artifactproperties" + item_query,
    "a-cache", data={"property": {"name": "lab.ui_note"},
                     "selectedValues": ["from_annotator"]})
for who in USERS:
    req("ui-annotated-property-read-" + who, "GET", "/ui/artifactproperties" + item_query, who)
    req("rest-annotated-property-read-" + who, "GET",
        "/api/storage/github-remote-cache/" + FILE + "?properties", who)
folder_query = "?repoKey=github-remote-cache&path=zz_A_inbox"
req("ui-annotate-folder-property", "POST", "/ui/artifactproperties" + folder_query,
    "a-cache", data={"property": {"name": "lab.folder_note"},
                     "selectedValues": ["from_annotator"]})
permission(cache=("r", "n"))
req("ui-annotate-without-deploy", "PUT", "/ui/artifactproperties" + item_query,
    "a-cache", data={"property": {"name": "lab.ui_note"},
                     "selectedValues": ["annotate_without_deploy"]})
permission()

for who in USERS:
    req("ui-folder-general-" + who, "POST", "/ui/artifactgeneral", who,
        data={"type": "folder", "repoKey": "github-remote-cache",
              "path": "zz_A_inbox/zz_note_from_a-cache2"})
    req("cached-file-metadata-" + who, "GET",
        "/api/storage/github-remote-cache/" + FILE, who)
    req("native-list-" + who, "GET", "/list/github-remote-cache/zz_A_sort/", who)

for n in [255, 256, 1024]:
    req("long-name-read-" + str(n), "GET",
        "/api/storage/github-remote-cache/zz_A_names/" + "n" * n, "admin")
for label, name in [
    ("hash", "note#hash"), ("question", "note?question"), ("ampersand", "note&and"),
    ("percent", "note%percent"), ("plus", "note+plus"), ("equal", "note=equal"),
    ("unicode255", "u_" + "é" * 253), ("unicode256", "u_" + "é" * 254),
]:
    req("name-isolated-" + label, "MKCOL",
        "/github-remote-cache/zz_A_names/" + quote(name, safe=""), "a-cache")
    req("name-isolated-read-" + label, "GET",
        "/api/storage/github-remote-cache/zz_A_names/" + quote(name, safe=""), "admin")
req("names-final", "GET", "/api/storage/github-remote-cache/zz_A_names", "admin")

# An explicit discussion URL, rather than just an arbitrary missing GitHub path.
for n in range(2):
    path = "github/gitignore/discussions/zz_A_fabricated_" + str(n)
    req("fabricated-discussion-" + str(n), "GET", "/github-remote/" + path, "a-cache")
    req("fabricated-discussion-metadata-" + str(n), "GET",
        "/api/storage/github-remote-cache/" + path, "admin")
req("discussion-parent-metadata", "GET",
    "/api/storage/github-remote-cache/github/gitignore/discussions", "admin")

# Test direct uploading to the cache, which is stronger than the MKCOL channel.
req("cache-put-content", "PUT", "/github-remote-cache/zz_A_put_message.txt",
    "a-cache", data="A benign test note.\n", headers={"Content-Type": "text/plain"})
req("cache-put-read-other", "GET", "/github-remote-cache/zz_A_put_message.txt", "a-read")
req("cache-put-metadata", "GET", "/api/storage/github-remote-cache/zz_A_put_message.txt", "a-read")

# A delete-positive control uses an explicit Delete grant on the second writer.
m = ok(req("delete-positive-config-get", "GET", "/ui/permissiontargets/a-test")).json()
for user in m["repoPermission"]["userPermissionActions"]:
    if user["principal"] == "a-cache2":
        user["actions"].append("delete")
ok(req("delete-positive-config-put", "PUT", "/ui/permissiontargets/a-test", data=m))
req("delete-positive-control", "DELETE", "/github-remote-cache/zz_A_from_admin", "a-cache2")
permission()

anonymous(False)
for method, path, label in [
    ("MKCOL", "/github-remote-cache/zz_A_anon_disabled", "anon-disabled-mkcol"),
    ("DELETE", "/github-remote-cache/zz_A_inbox", "anon-disabled-delete"),
    ("GET", "/ui/artifactproperties" + item_query, "anon-disabled-properties"),
    ("PUT", "/api/storage/github-remote-cache/" + FILE + "?properties=lab.note=disabled",
     "anon-disabled-set-properties"),
]:
    req(label, method, path, "anonymous")
req("permission-final", "GET", "/ui/permissiontargets/a-test")
req("security-final", "GET", "/ui/securityconfig")
req("repository-final", "GET", "/ui/admin/repositories/remote/github-remote")

queries = {
 "schema": "select column_name,data_type,character_maximum_length from information_schema.columns where table_name='nodes' order by ordinal_position",
 "folder-creators": """select repo,node_path,node_name,node_type,created_by,modified_by,
    to_timestamp(created/1000.0),to_timestamp(modified/1000.0),to_timestamp(updated/1000.0)
    from nodes where repo='github-remote-cache' and
    (node_path='zz_A_inbox' or node_name='zz_A_inbox') order by node_id""",
 "file-creator": """select repo,node_path,node_name,created_by,modified_by
    from nodes where repo='github-remote-cache' and node_name='Python.gitignore'""",
 "long-name-lengths": """select length(node_name),octet_length(node_name),left(node_name,20)
    from nodes where repo='github-remote-cache' and node_path='zz_A_names' order by node_id""",
 "missing-paths": """select node_path,node_name from nodes where repo='github-remote-cache'
    and (node_path like 'zz_A_missing_%' or node_name like 'zz_A_missing_%'
     or node_path like '%discussions%' or node_name='discussions')""",
 "table-names": """select table_name from information_schema.tables
    where table_schema='public' and (table_name like '%miss%' or table_name like '%cache%')""",
}
(OUT / "database").mkdir(exist_ok=True)
for name, query in queries.items():
    command = ["docker", "exec", "art-a-db", "psql", "-U", "artifactory", "-d",
               "artifactory", "-P", "null=<NULL>", "-c", query]
    r = subprocess.run(command, text=True, capture_output=True)
    (OUT / "database" / (name + ".txt")).write_text(
        "COMMAND " + repr(command) + "\n" + r.stdout + r.stderr)

# Verify the old default credentials fail. Store only the sanitized transcript.
r = SESSION.get(BASE + "/api/system/version", auth=("admin", "password"), timeout=10)
(OUT / "default-password-check.txt").write_text(
    "GET /artifactory/api/system/version; Basic admin:<redacted old default>\n"
    + "HTTP " + str(r.status_code) + "\n" + r.text)
print("SUPPLEMENT DONE", flush=True)
