#!/bin/bash
# usage: run_all.sh <run-name> <duration-s> <phase-split-s>
# Cleans the cache namespace, starts the 8 client containers, waits for them, snapshots the server logs.
RUN=$1; DUR=$2; SPLIT=$3
LAB=$HOME/workspaces/artifactory-lab/c
cd $LAB
python3 scripts/clean_cache.py
bash scripts/launch.sh $RUN $DUR $SPLIT 60
T0=$(cat gt/$RUN/t0)
END=$(python3 -c "print($T0+$DUR+45)")
while [ $(python3 -c "import time;print(int(time.time()<$END))") = 1 ]; do sleep 15; done
rm -rf logs-$RUN; mkdir logs-$RUN; sudo cp -r var/log/. logs-$RUN/; sudo chown -R $USER logs-$RUN
echo "RUN $RUN DONE $(date -u +%FT%TZ)"
