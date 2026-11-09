#!/bin/bash
# S4: two vLLM processes on one GPU, one per tenant, each behind its own salted gateway.
set -eux
cd ~/s1
export CUDA_HOME=/usr/local/cuda
export PATH=/usr/local/cuda/bin:$HOME/venv/bin:$PATH
for s in vllm vllm2 gw9001 gw9002 gw9003 gw9004 gw9101 gw9102; do tmux kill-session -t $s 2>/dev/null || true; done
sleep 5
tmux new-session -d -s vllm "~/venv/bin/vllm serve Qwen/Qwen2.5-1.5B-Instruct --host 127.0.0.1 --port 8000 --enable-prefix-caching --enable-prompt-tokens-details --gpu-memory-utilization 0.42 --max-model-len 8192 --seed 0 2>&1 | tee ~/s1/logs/vllm-s4-a.log"
until curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 2; done
tmux new-session -d -s vllm2 "~/venv/bin/vllm serve Qwen/Qwen2.5-1.5B-Instruct --host 127.0.0.1 --port 8001 --enable-prefix-caching --enable-prompt-tokens-details --gpu-memory-utilization 0.42 --max-model-len 8192 --seed 0 2>&1 | tee ~/s1/logs/vllm-s4-b.log"
until curl -sf http://127.0.0.1:8001/health >/dev/null; do sleep 2; done
tmux new-session -d -s gw9101 "~/venv/bin/python ~/s1/gateway.py --policy salted --port 9101 --upstream http://127.0.0.1:8000 --log ~/s1/logs/gw-s4-a.jsonl"
tmux new-session -d -s gw9102 "~/venv/bin/python ~/s1/gateway.py --policy salted --port 9102 --upstream http://127.0.0.1:8001 --log ~/s1/logs/gw-s4-b.jsonl"
sleep 5
nvidia-smi --query-compute-apps=pid,used_memory --format=csv | tee ~/s1/logs/s4-gpu-processes.csv
PY=~/venv/bin/python
$PY trials_s4.py run --split calibration
$PY trials_s4.py freeze
$PY trials_s4.py run --split holdout
echo S4_DONE
