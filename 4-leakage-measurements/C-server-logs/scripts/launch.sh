#!/bin/bash
# usage: launch.sh <run-name> <duration-s> <phase-split-s> [start-delay-s]
set -e
RUN=$1; DUR=$2; SPLIT=$3; DELAY=${4:-60}
LAB=$HOME/workspaces/artifactory-lab/c
GT=$LAB/gt/$RUN; mkdir -p $GT
T0=$(python3 -c "import time;print(round(time.time()+$DELAY,3))")
echo "$T0" > $GT/t0
i=21
for c in benign1 benign2 board1 board2 board3 board4 mixed1 mixed2; do
  role=${c%%[0-9]*}
  docker rm -f c-$c >/dev/null 2>&1 || true
  docker run -d --name c-$c --network artnet-c --ip 172.28.0.$i --memory 128m \
    -v $LAB/scripts:/s:ro -v $GT:/gt -v $LAB/clients-creds.json:/creds.json:ro \
    python:3.12-slim python3 -u /s/client.py --name $c --role $role --creds /creds.json --out /gt \
      --t0 $T0 --duration $DUR --phase-split $SPLIT $( [ $c = board2 ] && echo "--main-exit $((DUR*3/4+0))" ) >/dev/null
  echo "$c 172.28.0.$i" >> $GT/ips.txt
  i=$((i+1))
done
docker exec -d c-board2 python3 -u /s/client.py --name board2 --role board --creds /creds.json --out /gt \
    --t0 $T0 --duration $DUR --phase-split $SPLIT --heartbeat-only
echo started t0=$T0
