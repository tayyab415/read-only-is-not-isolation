#!/bin/bash
# Usage: serve.sh cache_on|cache_off   (starts vLLM on 127.0.0.1:8000 and three gateways)
set -eu
export CUDA_HOME=/usr/local/cuda
export PATH=/usr/local/cuda/bin:$HOME/venv/bin:$PATH
PHASE=$1
CACHE_FLAG=--enable-prefix-caching
[ "$PHASE" = cache_off ] && CACHE_FLAG=--disable-prefix-caching
mkdir -p ~/s1/logs
tmux kill-session -t vllm 2>/dev/null || true
for p in 9001 9002 9003; do tmux kill-session -t gw$p 2>/dev/null || true; done
tmux new-session -d -s vllm "~/venv/bin/vllm serve Qwen/Qwen2.5-1.5B-Instruct --host 127.0.0.1 --port 8000 $CACHE_FLAG --enable-prompt-tokens-details --gpu-memory-utilization 0.85 --max-model-len 8192 --seed 0 2>&1 | tee ~/s1/logs/vllm-$PHASE.log"
until curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 2; done
tmux new-session -d -s gw9001 "~/venv/bin/python ~/s1/gateway.py --policy shared --port 9001 --log ~/s1/logs/gw-$PHASE-shared.jsonl 2>&1 | tee ~/s1/logs/gw9001-$PHASE.out"
tmux new-session -d -s gw9002 "~/venv/bin/python ~/s1/gateway.py --policy salted --port 9002 --log ~/s1/logs/gw-$PHASE-salted.jsonl 2>&1 | tee ~/s1/logs/gw9002-$PHASE.out"
tmux new-session -d -s gw9003 "~/venv/bin/python ~/s1/gateway.py --policy passthrough --port 9003 --log ~/s1/logs/gw-$PHASE-passthrough.jsonl 2>&1 | tee ~/s1/logs/gw9003-$PHASE.out"
sleep 4; tmux ls
