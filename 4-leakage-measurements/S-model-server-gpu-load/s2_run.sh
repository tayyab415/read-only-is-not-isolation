#!/bin/bash
# S2 sequence. Waits for S1 to finish, restarts the server with prefix caching on,
# starts the salted gateway (9002) and the 500 ms floor gateway (9004) with S2 log files.
set -eux
cd ~/s1
export PATH=$HOME/venv/bin:$PATH
until grep -q "S1_DONE\|EXIT=" ~/s1/logs/run_all.log; do sleep 15; done
grep -q S1_DONE ~/s1/logs/run_all.log
cp ~/s1/logs/vllm-cache_on.log ~/s1/logs/vllm-cache_on-S1.log  # serve.sh below would overwrite it
~/s1/serve.sh cache_on
for p in 9001 9002 9003; do tmux kill-session -t gw$p 2>/dev/null || true; done
tmux new-session -d -s gw9002 "~/venv/bin/python ~/s1/gateway.py --policy salted --port 9002 --log ~/s1/logs/gw-floor-salted.jsonl"
tmux new-session -d -s gw9004 "~/venv/bin/python ~/s1/gateway_floor.py --policy salted --port 9004 --floor-ms 500 --log ~/s1/logs/gw-floor-floor500.jsonl"
sleep 5
PY=~/venv/bin/python
$PY trials_s2.py run --split calibration
$PY trials_s2.py freeze
$PY trials_s2.py run --split holdout
$PY trials_s2.py utility
echo S2_DONE
