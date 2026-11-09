#!/usr/bin/env bash
# Put Artifactory credentials into each sandbox's ~/.netrc (tmpfs, mode 600). Password never appears in argv or logs.
# usage: provision-sandbox.sh [HOST]   (HOST defaults to art-a)
HOST=${1:-art-a}
for n in 1 2 3; do
  user=harness-agent$n
  pw=$(cat ~/workspaces/artifactory-lab/d/creds/$user)
  printf 'machine %s login %s password %s\n' "$HOST" "$user" "$pw" |
    docker exec -i d-agent$n bash -c 'umask 077; cat > /home/agent/.netrc'
done
echo provisioned
