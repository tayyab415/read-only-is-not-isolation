#!/bin/bash
# Run as root on a node launched from the BC machine image (built from a provisioned n01).
# Fresh per-node secret, empty logs and keys, firewall, services, health. Writes /opt/bc/READY.
set -euxo pipefail
systemctl stop bc-gw1 bc-gw2 || true
rm -f /opt/bc/READY /opt/bc/logs/*
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > /opt/bc/secret; chmod 600 /opt/bc/secret
echo '{}' > /opt/bc/keys/slot1.json; echo '{}' > /opt/bc/keys/slot2.json
docker ps -aq --filter label=bc.episode | xargs -r docker rm -f
cp /tmp/bc-src/gateway_bc.py /tmp/bc-src/node_ctl.py /tmp/bc-src/firewall.sh /opt/bc/ 2>/dev/null || true
chmod 755 /opt/bc/firewall.sh
bash /opt/bc/firewall.sh
systemctl restart bc-vllm bc-gw1 bc-gw2
for i in $(seq 1 100); do curl -sf http://127.0.0.1:8000/health && break; sleep 3; done
curl -sf http://127.0.0.1:8000/health
nvidia-smi --query-gpu=name,uuid,driver_version --format=csv,noheader > /opt/bc/gpu.txt
sha256sum /opt/bc/gateway_bc.py /opt/bc/node_ctl.py /opt/bc/firewall.sh > /opt/bc/code-sha256.txt
date -u +%FT%TZ > /opt/bc/READY
