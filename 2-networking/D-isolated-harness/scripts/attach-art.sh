#!/usr/bin/env bash
# Connect art-a to each sandbox network. Idempotent. art-a keeps its own network and published ports.
for n in 1 2 3; do
  docker network connect --alias art-a d-net-$n art-a 2>&1 | grep -v "already exists" || true
done
docker inspect art-a --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}'
