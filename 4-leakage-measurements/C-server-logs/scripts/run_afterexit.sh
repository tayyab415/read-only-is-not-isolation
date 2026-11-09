#!/bin/bash
# Supplementary: board2's agent loop runs 4 min, then only the background heartbeat poster keeps running (12 more minutes).
# The container's PID 1 is "sleep" so the heartbeat outlives the agent loop.
LAB=$HOME/workspaces/artifactory-lab/c; cd $LAB
GT=$LAB/gt/afterexit; mkdir -p $GT
python3 scripts/clean_cache.py
T0=$(python3 -c "import time;print(round(time.time()+20,3))"); echo $T0 > $GT/t0
docker rm -f cx-board2 >/dev/null 2>&1
docker run -d --name cx-board2 --network artnet-c --ip 172.28.0.51 --memory 128m -v $LAB/scripts:/s:ro -v $GT:/gt -v $LAB/clients-creds.json:/creds.json:ro \
  python:3.12-slim sleep 1200 >/dev/null
docker exec -d cx-board2 python3 -u /s/client.py --name board2 --role board --creds /creds.json --out /gt --t0 $T0 --duration 240 --phase-split 99999
docker exec -d cx-board2 python3 -u /s/client.py --name board2 --role board --creds /creds.json --out /gt --t0 $T0 --duration 960 --phase-split 99999 --heartbeat-only
sleep 1020
rm -rf logs-afterexit; mkdir logs-afterexit; sudo cp -r var/log/. logs-afterexit/; sudo chown -R $USER logs-afterexit
docker rm -f cx-board2 >/dev/null
echo "AFTEREXIT DONE $(date -u +%FT%TZ)"
