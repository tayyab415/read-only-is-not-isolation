"""Scoped native checksum, source and request checks for owned P9 fixtures."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PAYLOAD_A = b"p1-meter fixture package bytes\n"
PAYLOAD_B = b"q1-meter fixture package bytes\n"
IMAGE = "sha256:d103c0464d33645acdcbfbb6fc61310d7faf14c9893975cbee562487eaeac1d1"
CELLS = ("equal", "distinct", "aa-equal", "aa-distinct")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def payload(cell, number):
    require(cell in CELLS and 0 <= number < 16, "Invalid fixed content assignment")
    return PAYLOAD_B if "distinct" in cell and number == 0 else PAYLOAD_A


def rendered_upstream(cell):
    require(cell in CELLS, "Invalid fixed cell")
    text = (HERE / "upstream_content.py").read_text()
    require(text.count("DISTINCT = False") == 1, "The fixed rendering seam changed")
    return text.replace("DISTINCT = False",
                        "DISTINCT = True" if "distinct" in cell else "DISTINCT = False")


def validate_mapping(record, public):
    try:
        cell = public["arm"]
        repos = {repo + "-cache" for repo in public["repositories"].values()}
        nodes, blobs = record["nodes"], record["blobs"]
        require(record["passed"] and record["artifactory_image"] == IMAGE,
                "Native mapping/image failed")
        require(len(nodes) == 32 and
                {(n["repo"], n["node_name"]) for n in nodes} ==
                {(repo, f"pkg-{number:02d}.bin") for repo in repos for number in range(16)},
                "Native node inventory differs")
        wanted = {}
        for number in range(16):
            body = payload(cell, number)
            wanted[f"pkg-{number:02d}.bin"] = {
                "sha1": hashlib.sha1(body).hexdigest(), "sha256": digest(body)}
        require(all(node["node_path"] == "." and node["bin_length"] == 31
                    and node["node_type"] == 1
                    and node["sha1_actual"] == wanted[node["node_name"]]["sha1"]
                    and node["sha256"] == wanted[node["node_name"]]["sha256"]
                    for node in nodes), "Native object content mapping differs")
        checksums = {item["sha1"]: item["sha256"] for item in wanted.values()}
        require(set(blobs) == set(checksums), "Canonical blob inventory differs")
        for checksum, blob in blobs.items():
            require(blob["sha1"] == checksum and blob["sha256"] == checksums[checksum]
                    and blob["stat"]["bytes"] == 31
                    and blob["stat"]["kind"] == "regular file"
                    and blob["path"] in (
                        "/var/opt/jfrog/artifactory/data/artifactory/filestore/"
                        + checksum[:2] + "/" + checksum,
                        "/var/opt/jfrog/artifactory/filestore/"
                        + checksum[:2] + "/" + checksum),
                    "Canonical blob bytes/path differ")
        require(len({(b["stat"]["device"], b["stat"]["inode"]) for b in blobs.values()})
                == len(checksums), "Distinct expected blobs share a file identity")
        return True
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False


def collect_mapping(public):
    # Trusted controller reads only the fresh fixture's declared cache nodes.
    import p2b
    old = p2b.old
    record = {"utc": datetime.now(timezone.utc).isoformat(), "passed": False,
              "run_id": public["run_id"], "cell": public["arm"],
              "nodes": [], "blobs": {}}
    try:
        run = public["run_id"]
        require(bool(re.fullmatch(r"p9-[sch]1-(equal|distinct|aa-equal|aa-distinct)", run)),
                "Not an owned prospective P9 run")
        repos = [public["repositories"][actor] + "-cache" for actor in ("alice", "bob")]
        require(repos == [f"p1-{run}-{actor}-cache" for actor in ("alice", "bob")],
                "Unexpected owned cache repository")
        quoted = ",".join("'" + repo + "'" for repo in repos)
        sql = (
            "BEGIN READ ONLY; SELECT coalesce(json_agg(row_to_json(n) "
            "ORDER BY repo,node_path,node_name),'[]'::json) FROM "
            "(SELECT node_id,node_type,repo,node_path,node_name,bin_length,sha1_actual,sha256 "
            "FROM public.nodes WHERE repo IN (" + quoted + ") "
            "AND node_name ~ '^pkg-[0-9]{2}\\.bin$') n; COMMIT;")
        record["sql"] = sql
        record["nodes"] = json.loads(old.run_ok([
            "docker", "exec", "art-a-db", "psql", "-U", "artifactory",
            "-d", "artifactory", "-qAt", "-c", sql]))
        checksums = {hashlib.sha1(payload(public["arm"], number)).hexdigest()
                     for number in range(16)}
        for checksum in sorted(checksums):
            candidates = [
                "/var/opt/jfrog/artifactory/data/artifactory/filestore/"
                + checksum[:2] + "/" + checksum,
                "/var/opt/jfrog/artifactory/filestore/" + checksum[:2] + "/" + checksum]
            found = []
            for path in candidates:
                status = old.run(["docker", "exec", "art-a", "test", "-f", path])
                require(status.returncode in (0, 1), "Native file-existence check failed")
                if status.returncode == 0:
                    found.append(path)
            require(len(found) == 1, "Expected one scoped canonical checksum file")
            path = found[0]
            sha1 = old.run_ok(["docker", "exec", "art-a", "sha1sum", path]).split()[0]
            sha256 = old.run_ok(["docker", "exec", "art-a", "sha256sum", path]).split()[0]
            device, inode, size, links, kind = old.run_ok([
                "docker", "exec", "art-a", "stat", "--printf=%d|%i|%s|%h|%F", path]).split("|")
            record["blobs"][checksum] = {
                "path": path, "sha1": sha1, "sha256": sha256,
                "stat": {"device": device, "inode": inode, "bytes": int(size),
                         "hardlinks": int(links), "kind": kind}}
        record["artifactory_image"] = old.run_ok([
            "docker", "inspect", "art-a", "--format", "{{.Image}}"]).strip()
        record["passed"] = True
        require(validate_mapping(record, public), "Complete native mapping validation failed")
    except Exception as error:
        record.update(passed=False, failure_type=type(error).__name__,
                      failure=str(error)[:500])
    return record


def validate_public_evidence(public, trials, before, after):
    intervention = public["content_intervention"]
    expected = rendered_upstream(public["arm"])
    mount = intervention["upstream_mount"]
    checks = {
        "fixed rendered source": intervention["rendered_upstream_source"] == expected
            and intervention["rendered_upstream_sha256"] == digest(expected.encode()),
        "fixed cell content": intervention["cell"] == public["arm"]
            and intervention["distinct_hot_objects"] == ("distinct" in public["arm"]),
        "read-only upstream source bind": mount["Destination"] == "/upstream.py"
            and mount["Type"] == "bind" and not mount["RW"]
            and mount["Source"].endswith("/P9-content/fixture-sources/"
                                         + public["run_id"] + "/upstream.py"),
        "native before mapping": validate_mapping(before, public),
        "native after mapping": validate_mapping(after, public),
        "stable native references": before["nodes"] == after["nodes"],
        "stable canonical file identities": before["blobs"] == after["blobs"],
        "stable receiver ETag": public["fixture_etag"] ==
            public["receiver_etag_readback"]["etag"]
            and public["receiver_etag_readback"]["status"] == 200
            and public["receiver_etag_readback"]["body_sha256"] == digest(PAYLOAD_A)
            and public["fixture_etag"].strip('"') == hashlib.sha1(PAYLOAD_A).hexdigest(),
    }
    for trial in trials:
        for row in trial["sender_records"]:
            checks["sender expected bytes " + row["marker"]] = (
                row["method"] == "GET" and row["status"] == 200
                and row["body_length"] == 31
                and row["body_sha256"] == digest(payload(public["arm"], 0)))
        for row in trial["post"]:
            if row["path"].partition("?")[0].endswith("/pkg-15.bin"):
                headers = {key.lower(): value for key, value in row["headers"].items()}
                checks["receiver stable hot object " + row["marker"]] = (
                    row["status"] == 200 and headers.get("etag") == public["fixture_etag"]
                    and row["body_length"] == (31 if row["method"] == "GET" else 0)
                    and row["body_sha256"] == digest(
                        PAYLOAD_A if row["method"] == "GET" else b""))
    return checks
