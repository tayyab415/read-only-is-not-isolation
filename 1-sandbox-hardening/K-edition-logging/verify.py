#!/usr/bin/env python3
"""Recheck the exported edition experiments without service or cloud access."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
audit = json.loads((DATA / "redaction.json").read_text())
assert audit["remaining_known_secret_matches"] == 0
for name, digest in audit["artifact_sha256"].items():
    assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest, name
summary = {}
for edition, license_name in (("oss", "Artifactory OSS"), ("jcr", "JFrog Container Registry")):
    name = edition+"-7.133.6-routed"
    dest = DATA / name
    requests = [json.loads(line) for line in (dest / "requests.jsonl").read_text().splitlines()]
    assert len({r["user_agent"] for r in requests}) == len(requests)
    by_id = {r["id"]: r for r in requests}
    art = {}
    for line in (dest / "artifactory-request.log").read_text().splitlines():
        fields = line.split("|")
        art.setdefault(fields[-1], []).append(fields)
    router = {}
    for line in (dest / "router-request.log").read_text().splitlines():
        row = json.loads(line)
        router.setdefault(row.get("request_User-Agent"), []).append(row)
    traces = {}
    for r in requests:
        assert hashlib.sha256(r["response_body"].encode()).hexdigest() == r["response_sha256"]
        assert len(art[r["user_agent"]]) == len(router[r["user_agent"]]) == 1, r["id"]
        a, s = art[r["user_agent"]][0], router[r["user_agent"]][0]
        assert a[3] == r["identity"] and a[4] == r["method"] and int(a[6]) == r["status"]
        assert a[5] == r["path"].split("?")[0]
        assert s["RequestMethod"] == r["method"] and s["DownstreamStatus"] == r["status"]
        assert s["RequestPath"] == "/artifactory"+r["path"]
        assert a[1] == s["TraceId"]
        traces[r["id"]] = a[1]
    version = json.loads(by_id["version"]["response_body"])
    assert (version["version"], version["revision"], version["license"]) == (
        "7.133.6", "83306900", license_name)
    grants = json.loads(by_id["grant-readback"]["response_body"])["repoPermission"]
    assert {r["principal"]: set(r["actions"]) for r in grants["userPermissionActions"]} == {
        "k-read": {"read"}, "k-cache": {"read", "upload"}}
    assert set(grants["repoKeys"]) == {"k-local", "k-remote"}
    access = (dest / "artifactory-access.log").read_text()
    nodes = (dest / "database-nodes.txt").read_text().splitlines()
    for repo in ("k-local", "k-remote"):
        cached = repo+"-cache" if repo.endswith("remote") else repo
        assert by_id[repo+"-mkcol-k-read"]["status"] == 403
        assert by_id[repo+"-folder-info-k-read"]["status"] == 404
        assert by_id[repo+"-mkcol-k-cache"]["status"] == 201
        assert by_id[repo+"-folder-info-k-cache"]["status"] == 200
        assert json.loads(by_id[repo+"-folder-info-k-cache"]["response_body"])["path"] == "/folder-k-cache"
        assert cached+"|.|folder-k-cache|0||" in nodes
        assert by_id[repo+"-stats"]["status"] == 200
        assert "[DENIED DEPLOY] "+cached+":folder-k-read" in access
        assert not any("ACCEPTED DEPLOY" in line and "folder-k-cache" in line
                       for line in access.splitlines())
        assert "[ACCEPTED DEPLOY] "+cached+":fixture.bin" in access
    image = json.loads((dest / "image.json").read_text())[0]
    summary[edition] = {"license": license_name, "version": version["version"],
        "revision": version["revision"], "image_digests": image["RepoDigests"],
        "verified_requests": len(requests), "verified_request_router_trace_joins": len(traces),
        "generic_local_and_remote_repositories": True,
        "read_only_mkcol": 403, "read_upload_mkcol": 201,
        "folder_metadata_confirms_creation": True,
        "folder_database_creator_modifier": "NULL",
        "accepted_folder_deploy_events": 0,
        "positive_file_deploy_controls": 2,
        "deep_listing_statuses": [by_id[r+"-list"]["status"] for r in ("k-local", "k-remote")]}
cleanup = json.loads((ROOT / "cleanup.json").read_text())
assert cleanup["final_state"] == "terminated" and cleanup["security_group_deleted"]
assert all(s == "absent" for s in cleanup["volume_states"].values())
result = {"editions": summary, "total_verified_requests": sum(v["verified_requests"] for v in summary.values()),
          "redaction_manifest_verified": True, "cloud_cleanup_verified": True,
          "limits": ["Controller uses in-container localhost curl, not a sandbox network client.",
                     "Deep listing returned 400; basic directory enumeration was not tested.",
                     "One fresh instance per edition at this version, not an edition-wide guarantee.",
                     "Initial readiness failures are retained and not product-capability evidence."]}
(ROOT / "verification.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
