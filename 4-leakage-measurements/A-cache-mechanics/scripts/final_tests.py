#!/usr/bin/env python3
import base64
import hashlib
import json
import subprocess
from urllib.parse import quote
from common import LAB, OUT, ok, permission, req

FILE = "github/gitignore/raw/refs/heads/main/Python.gitignore"
permission(cache=("n",))
req("annotate-only-property", "PUT",
    "/ui/artifactproperties?repoKey=github-remote-cache&path=" + quote(FILE, safe=""),
    "a-cache", data={"property": {"name": "lab.ui_note"},
                     "selectedValues": ["annotate_only_no_read_or_deploy"]})
req("annotate-only-property-read", "GET",
    "/api/storage/github-remote-cache/" + FILE + "?properties", "a-cache")
req("annotate-only-mkcol", "MKCOL", "/github-remote-cache/zz_A_annotate_only", "a-cache")
permission()

payload = b"Benign A lab test: a small file carried only in folder names.\n"
encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
root = "/github-remote-cache/zz_A_file_chunks"
ok(req("chunks-parent", "MKCOL", root, "a-cache"))
chunks = [encoded[i:i + 24] for i in range(0, len(encoded), 24)]
for i, chunk in enumerate(chunks):
    ok(req("chunks-write-" + str(i), "MKCOL", root + f"/{i:04}_{chunk}", "a-cache"))
r = ok(req("chunks-read-other", "GET", "/api/storage" + root, "a-read"))
reconstructed = "".join(x["uri"].split("_", 1)[1] for x in
                        sorted(r.json()["children"], key=lambda x: x["uri"]))
decoded = base64.urlsafe_b64decode(reconstructed + "=" * (-len(reconstructed) % 4))
(OUT / "chunk-roundtrip.json").write_text(json.dumps({
    "writer": "a-cache", "reader": "a-read", "chunks": len(chunks),
    "original_sha256": hashlib.sha256(payload).hexdigest(),
    "reconstructed_sha256": hashlib.sha256(decoded).hexdigest(),
    "matches": decoded == payload}, indent=2))
assert payload == decoded

req("encoded-slash-name", "MKCOL",
    "/github-remote-cache/zz_A_names/note%2Fslash", "a-cache")
req("encoded-slash-metadata", "GET",
    "/api/storage/github-remote-cache/zz_A_names/note/slash", "admin")
req("dot-name-prepared", "MKCOL", "/github-remote-cache/zz_A_names/%2E", "a-cache")
req("dotdot-name-prepared", "MKCOL", "/github-remote-cache/zz_A_names/%2E%2E", "a-cache")

permission(cache=("d",))
req("delete-only-other-user-folder", "DELETE",
    "/github-remote-cache/zz_A_inbox/zz_note_from_a-cache2", "a-cache")
permission()
req("final-permission-verified", "GET", "/ui/permissiontargets/a-test")
req("final-version", "GET", "/api/system/version")
req("final-health", "GET", "/api/system/ping")

state = subprocess.check_output([
    "docker", "inspect", "art-a", "--format",
    '{{.State.Status}} oom={{.State.OOMKilled}} restart={{.RestartCount}} '
    'ports={{json .NetworkSettings.Ports}} networks={{json .NetworkSettings.Networks}}'],
    text=True)
resource = subprocess.check_output([
    "docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}",
    "art-a", "art-a-db"], text=True)
(OUT / "running-state.txt").write_text(state + resource)
java = subprocess.check_output(["docker", "exec", "art-a", "sh", "-c",
    "ps -eo args | grep '[j]ava' | sed -E 's/ -D[^ ]+/ /g'"], text=True)
(OUT / "java-processes.txt").write_text(java)
print("FINAL TESTS DONE", flush=True)
