#!/usr/bin/env bash
# From inside d-agent1: Artifactory must be reachable, and nothing else on art-a's other networks.
SB=${SB:-d-agent1}; N=${SB#d-agent}
x() { printf '\n$ %s\n' "$1"; docker exec "$SB" bash -c "$1" 2>&1 | head -${2:-6} | cut -c1-220; echo "rc=${PIPESTATUS[0]}"; }
echo "=== $SB -> Artifactory ($(date -u +%FT%TZ))"
x "getent hosts art-a"
x "curl -s -m 5 -w ' HTTP %{http_code}\n' http://art-a:8082/artifactory/api/system/ping"
x "curl -s -m 5 -w ' HTTP %{http_code}\n' http://art-a:8081/artifactory/api/system/ping"
x "curl -s -m 5 -o /dev/null -w 'no auth, repo listing: HTTP %{http_code}\n' http://art-a:8082/artifactory/github-remote/"
x "curl -s -m 5 -n -w ' HTTP %{http_code}\n' http://art-a:8082/artifactory/api/repositories"
x "curl -s -m 5 -n -w ' HTTP %{http_code}\n' http://art-a:8082/artifactory/api/system/version" 8
x "curl -s -m 5 -n -o /dev/null -w 'admin-only api/security/users: HTTP %{http_code}\n' http://art-a:8082/artifactory/api/security/users"
echo; echo "=== nothing else reachable from $SB"
ART_OTHER=$(docker inspect art-a --format '{{(index .NetworkSettings.Networks "art-a-net").IPAddress}}')
DB=$(docker inspect art-a-db --format '{{(index .NetworkSettings.Networks "art-a-net").IPAddress}}')
x "nc -zv -w 3 $ART_OTHER 8082   # art-a's other interface (art-a-net)"
x "nc -zv -w 3 $DB 5432   # art-a postgres"
x "nc -zv -w 3 127.0.0.1 18584   # art-a published port on lab host loopback"
x "getent hosts art-a-db"
for o in 1 2 3; do [ "$o" = "$N" ] && continue; x "nc -zv -w 3 172.30.1$o.2 8082   # art-a via the OTHER sandbox's network"; x "nc -zv -w 3 172.30.1$o.1 22   # other sandbox"; done
x "ip route"
x "curl -s -m 5 -o /dev/null -w 'internet: HTTP %{http_code}\n' https://example.com"
docker exec art-a sh -c 'cat /proc/sys/net/ipv4/ip_forward' | sed 's/^/art-a ip_forward=/'
