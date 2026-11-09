#!/usr/bin/env bash
# Real npm and pip installs from inside d-agent1 through art-a's only remote (github-remote, Generic, github.com).
# The sandbox has no internet. User harness-agent1 has Read + Deploy/Cache. Not PyPI or the npm registry:
# these install GitHub source archives. Runs on the lab host.
SB=d-agent1; U=http://art-a:8082/artifactory/github-remote
x() { printf '\n$ %s\n' "$1"; docker exec "$SB" bash -c "$1" 2>&1 | tail -${2:-8} | cut -c1-240; }
echo "=== $(date -u +%FT%TZ)"
# npm needs credentials for the tarball URL, which is not under a registry prefix: host-level _auth in ~/.npmrc.
pw=$(cat ~/workspaces/artifactory-lab/d/creds/harness-agent1)
printf '//art-a:8082/:_auth=%s\nfund=false\naudit=false\nupdate-notifier=false\n' "$(printf 'harness-agent1:%s' "$pw" | base64 -w0)" | docker exec -i $SB bash -c 'umask 077; cat > ~/.npmrc'
echo "--- npm: install jonschlinkert/is-number 7.0.0 from a GitHub tag archive"
x "mkdir -p ~/nm && cd ~/nm && rm -rf node_modules package*.json && npm init -y >/dev/null && npm install --no-audit --no-fund $U/jonschlinkert/is-number/archive/refs/tags/7.0.0.tar.gz 2>&1 | tail -4; ls node_modules; node -e \"console.log('is-number(5)=',require('is-number')(5), 'is-number(\\\"x\\\")=',require('is-number')('x'))\""
echo "--- pip: install benjaminp/six 1.16.0 from a GitHub tag archive (no build isolation, no deps)"
x "rm -rf ~/pylib; pip install --no-cache-dir --disable-pip-version-check --no-build-isolation --no-deps --target ~/pylib $U/benjaminp/six/archive/refs/tags/1.16.0.tar.gz 2>&1 | tail -4; PYTHONPATH=~/pylib python3 -c 'import six; print(\"six\", six.__version__, six.__file__)'"
echo "--- second npm install of the same tag: should be served from Artifactory's cache"
x "cd ~/nm && rm -rf node_modules package-lock.json && npm install --no-audit --no-fund $U/jonschlinkert/is-number/archive/refs/tags/7.0.0.tar.gz 2>&1 | tail -2"
echo "--- what Artifactory logged for these installs (art-a request log, client 172.30.11.1)"
sudo grep -E '\|172\.30\.11\.1\|harness-agent1\|' ~/workspaces/artifactory-lab/a/var/log/artifactory-request.log | grep -E 'is-number|six' | cut -d'|' -f1,5,6,7,8,9,10,11 | tail -12
echo "--- cache entries created"
docker exec $SB bash -c "curl -s -n $U-cache/jonschlinkert/is-number/archive/refs/tags/ ; echo; curl -s -n $U-cache/benjaminp/six/archive/refs/tags/" | sed 's/<[^>]*>//g' | grep -E 'tar.gz|^\s*$' | head -6
