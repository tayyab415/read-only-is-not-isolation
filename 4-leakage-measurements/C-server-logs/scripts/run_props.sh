#!/bin/bash
# Property-channel extension, four clients. usage: run_props.sh [duration-s] [run-name] [writer-base-path]
DUR=${1:-720}; RUN=${2:-props}; BASE=${3:-/artifactory/github-remote-cache}
LAB=$HOME/workspaces/artifactory-lab/c; cd $LAB
GT=$LAB/gt/$RUN; mkdir -p $GT
python3 scripts/clean_cache.py
T0=$(python3 -c "import time;print(round(time.time()+30,3))"); echo $T0 > $GT/t0
i=41
for spec in board3:prop_writer board1:prop_reader benign2:ci benign1:ci_query; do
  n=${spec%%:*}; m=${spec##*:}
  docker rm -f cp-$n >/dev/null 2>&1
  docker run -d --name cp-$n --network artnet-c --ip 172.28.0.$i --memory 64m -v $LAB/scripts:/s:ro -v $GT:/gt -v $LAB/clients-creds.json:/creds.json:ro \
    python:3.12-slim python3 -u /s/props_ext.py --name $n --mode $m --creds /creds.json --out /gt --t0 $T0 --duration $DUR --base $BASE >/dev/null
  i=$((i+1))
done
sleep $((DUR+60))
rm -rf logs-$RUN; mkdir logs-$RUN; sudo cp -r var/log/. logs-$RUN/; sudo chown -R $USER logs-$RUN
echo "PROPS $RUN DONE $(date -u +%FT%TZ)"
