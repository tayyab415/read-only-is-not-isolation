#!/bin/bash
# BC node provisioning. Run as root from /tmp/bc-src (files copied by launch_fleet.py).
# Writes /opt/bc/PROVISIONED on success; log in /opt/bc/provision.log.
set -euxo pipefail
SRC=/tmp/bc-src
mkdir -p /opt/bc/logs /opt/bc/keys
cp $SRC/gateway_bc.py $SRC/node_ctl.py $SRC/node_ctl.sh $SRC/constraints.txt $SRC/actor.Dockerfile /opt/bc/
chmod 755 /opt/bc/node_ctl.sh
[ -f /opt/bc/secret ] || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > /opt/bc/secret
chmod 600 /opt/bc/secret
echo '{}' > /opt/bc/keys/slot1.json; echo '{}' > /opt/bc/keys/slot2.json
chmod 700 /opt/bc/keys
command -v docker || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io)
systemctl enable --now docker
# Python env for vLLM and the gateway, pinned to the S1 freeze.
[ -x /root/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh
/root/.local/bin/uv venv -p 3.12 /opt/bc/venv --allow-existing
/root/.local/bin/uv pip install -p /opt/bc/venv/bin/python -c /opt/bc/constraints.txt vllm==0.33.0 fastapi uvicorn httpx numpy
HF_HOME=/opt/bc/hf /opt/bc/venv/bin/hf download Qwen/Qwen2.5-1.5B-Instruct > /opt/bc/logs/hf.log 2>&1
# Actor image.
docker build -t bc-actor:1 -f /opt/bc/actor.Dockerfile /opt/bc > /opt/bc/logs/docker-build.log 2>&1
docker image inspect bc-actor:1 --format '{{.Id}}' > /opt/bc/actor-image-id
docker run --rm bc-actor:1 pip freeze > /opt/bc/actor-pip-freeze.txt
# Slot networks: internal bridges; the host side of each bridge is the slot gateway IP.
for s in 1 2; do
  docker network inspect bcnet$s >/dev/null 2>&1 || docker network create --internal \
    --subnet 10.201.$s.0/24 --gateway 10.201.$s.1 -o com.docker.network.bridge.name=bcbr$s bcnet$s
done
cp $SRC/firewall.sh /opt/bc/firewall.sh && chmod 755 /opt/bc/firewall.sh
bash /opt/bc/firewall.sh
# Services.
cat > /etc/systemd/system/bc-vllm.service <<'UNIT'
[Unit]
Description=BC vLLM (S1 configuration)
After=network.target
[Service]
Environment=HF_HOME=/opt/bc/hf
Environment=HF_HUB_OFFLINE=1
Environment=VLLM_SERVER_DEV_MODE=1
Environment=CUDA_HOME=/usr/local/cuda
Environment=PATH=/usr/local/cuda/bin:/opt/bc/venv/bin:/usr/bin:/bin
ExecStart=/opt/bc/venv/bin/vllm serve Qwen/Qwen2.5-1.5B-Instruct --served-model-name qwen2.5-1.5b-instruct --host 127.0.0.1 --port 8000 --enable-prefix-caching --enable-prompt-tokens-details --gpu-memory-utilization 0.85 --max-model-len 8192 --seed 0
StandardOutput=append:/opt/bc/logs/vllm.log
StandardError=append:/opt/bc/logs/vllm.log
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
for s in 1 2; do cat > /etc/systemd/system/bc-gw$s.service <<UNIT
[Unit]
Description=BC gateway slot $s
After=docker.service bc-vllm.service
[Service]
ExecStart=/opt/bc/venv/bin/python /opt/bc/gateway_bc.py --slot $s --bind 10.201.$s.1 --keyfile /opt/bc/keys/slot$s.json --secret /opt/bc/secret --log /opt/bc/logs/gw-slot$s.jsonl
StandardOutput=append:/opt/bc/logs/gw$s.out
StandardError=append:/opt/bc/logs/gw$s.out
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
UNIT
done
cat > /etc/systemd/system/bc-firewall.service <<'UNIT'
[Unit]
Description=BC slot firewall
After=docker.service
Requires=docker.service
Before=bc-gw1.service bc-gw2.service
[Service]
Type=oneshot
ExecStartPre=/bin/sleep 3
ExecStart=/opt/bc/firewall.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now bc-firewall
systemctl enable --now bc-vllm bc-gw1 bc-gw2
for i in $(seq 1 120); do curl -sf http://127.0.0.1:8000/health && break; sleep 3; done
curl -sf http://127.0.0.1:8000/health
curl -sf -X POST http://127.0.0.1:8000/reset_prefix_cache -o /dev/null -w '%{http_code}\n'
ip -4 addr show bcbr1 | grep 10.201.1.1
ip -4 addr show bcbr2 | grep 10.201.2.1
/opt/bc/venv/bin/pip freeze 2>/dev/null > /opt/bc/node-freeze.txt || /root/.local/bin/uv pip freeze -p /opt/bc/venv/bin/python > /opt/bc/node-freeze.txt
nvidia-smi --query-gpu=name,uuid,driver_version --format=csv,noheader > /opt/bc/gpu.txt
date -u +%FT%TZ > /opt/bc/PROVISIONED
