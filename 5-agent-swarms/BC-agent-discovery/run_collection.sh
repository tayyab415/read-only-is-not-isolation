#!/bin/bash
# BC main collection. Run from BC/ after FREEZE.json exists and freeze.py --check passes.
# Usage: HF_TOKEN_FILE=<path> ./run_collection.sh
set -euo pipefail
cd "$(dirname "$0")"
python3 freeze.py --check
python3 validation/fleet_readback.py --out validation/fleet-readback-precollection.json
python3 -c "
import json; n=[{'name':x['name'],'ssh':x['ssh']} for x in json.load(open('node/nodes.json')) if x.get('state')=='running']
json.dump(n, open('data/nodes-collection.json','w'), indent=1); print(len(n), 'nodes')"
export HF_TOKEN="$(cat "$HF_TOKEN_FILE")"
exec caffeinate -i python3 harness/fleet.py --schedule tasks/schedule.json --nodes data/nodes-collection.json \
  --out data --prompts harness/glue.py --fixtures harness/glue.py --max-parallel 10 \
  --cap 8.50 --episode-max-usd 0.30 --stop-at-utc 2026-11-09T08:30:00Z
