#!/usr/bin/env bash
set -euo pipefail
LAB="$HOME/workspaces/artifactory-lab/a"
mkdir -p "$LAB/var/etc" "$LAB/evidence" "$LAB/scripts"
free -h > "$LAB/evidence/resources-before.txt"
docker pull releases-docker.jfrog.io/jfrog/artifactory-oss:7.161.26
docker image inspect releases-docker.jfrog.io/jfrog/artifactory-oss:7.161.26 \
  --format '{{json .RepoDigests}} {{json .Config.Env}}' > "$LAB/evidence/image.txt"
cat > "$LAB/var/etc/system.yaml" <<'EOF'
configVersion: 1
shared:
  extraJavaOpts: "-Xms512m -Xmx1536m"
EOF
sudo chown -R 1030:1030 "$LAB/var"
docker run -d --name art-a --memory=4g --memory-swap=4g \
  --restart unless-stopped \
  -p 127.0.0.1:18583:8081 -p 127.0.0.1:18584:8082 \
  -v "$LAB/var:/var/opt/jfrog/artifactory" \
  -e JF_SHARED_EXTRAJAVAOPTS="-Xms512m -Xmx1536m" \
  releases-docker.jfrog.io/jfrog/artifactory-oss:7.161.26
docker inspect art-a --format '{{json .HostConfig}}' > "$LAB/evidence/container-hostconfig.json"
