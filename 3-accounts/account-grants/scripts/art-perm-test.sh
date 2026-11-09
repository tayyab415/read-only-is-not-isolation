#!/usr/bin/env bash
# What can a harness user (Read + Deploy/Cache on github-remote) actually do? Run from inside d-agent1.
SB=${SB:-d-agent1}
x() { printf '\n$ %s\n' "$1"; docker exec "$SB" bash -c "$1" 2>&1 | head -${2:-8} | cut -c1-240; }
U=http://art-a:8082/artifactory
echo "=== $SB permission behaviour ($(date -u +%FT%TZ))"
x "curl -s -m 60 -n -o /tmp/hw.tgz -w 'read-through fetch via github-remote: HTTP %{http_code} size=%{size_download} time=%{time_total}s\n' $U/github-remote/octocat/Hello-World/archive/refs/heads/master.tar.gz; file -b /tmp/hw.tgz | head -c 80; echo; tar tzf /tmp/hw.tgz | head -3"
x "curl -s -m 20 -n -w ' HTTP %{http_code}\n' $U/api/storage/github-remote-cache/octocat/Hello-World/archive/refs/heads" 20
x "curl -s -m 20 -n -o /dev/null -w 'second fetch (cache hit): HTTP %{http_code} time=%{time_total}s\n' $U/github-remote/octocat/Hello-World/archive/refs/heads/master.tar.gz"
echo; echo "=== writes (footprint on A's cache kept to one folder, prefix zz_D_harness_probe)"
x "curl -s -m 10 -n -X MKCOL -w ' MKCOL github-remote-cache/zz_D_harness_probe/: HTTP %{http_code}\n' $U/github-remote-cache/zz_D_harness_probe/"
x "curl -s -m 10 -n -X MKCOL -w ' MKCOL github-remote-cache/zz_D_harness_probe/child/ (nested): HTTP %{http_code}\n' $U/github-remote-cache/zz_D_harness_probe/child/"
x "curl -s -m 10 -n -w ' GET folder info: HTTP %{http_code}\n' $U/api/storage/github-remote-cache/zz_D_harness_probe" 12
x "curl -s -m 10 -n -w ' GET example-repo-local (A local repo, no permission): HTTP %{http_code}\n' $U/example-repo-local/"
x "curl -s -m 10 -n -X DELETE -w ' DELETE cached file (no Delete permission): HTTP %{http_code}\n' $U/github-remote-cache/octocat/Hello-World/archive/refs/heads/master.tar.gz"
