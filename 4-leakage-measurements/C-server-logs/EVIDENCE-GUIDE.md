# Evidence for study C

Results: see `README.md` in this folder.

Reproduce the numbers (Python 3 with numpy and pandas, run with `-I`):

    cd analysis
    python3 -I detect.py ../data/ds-main 2250 /tmp/results-main.json     # D1 to D4 (2250 = phase split in seconds)
    python3 -I clientsummary.py ../data/ds-main /tmp/clientsummary.json
    python3 -I thin.py ../data/ds-main /tmp/thin.json
    python3 -I sensitivity.py /tmp/sensitivity.json
    python3 -I props_eval.py ../data/ds-props3 /tmp/props3.json
    python3 -I afterexit_eval.py ../data/ds-afterexit 240 /tmp/afterexit.json

Rebuild a dataset from raw logs and ground truth (needs the full log files, see `logs-raw/`):

    python3 build_dataset.py <logs-dir> <gt-dir> <out-dir>

Run the traffic on the labhost (art-c must be up and provisioned):

    scripts/provision.py     # users
    scripts/seed.py          # fake packages, clients-creds.json
    scripts/run_all.sh main 4500 2250
    scripts/run_props.sh 480 props3 /artifactory/pkg-local/board
    scripts/run_afterexit.sh

Dataset columns: `s_*` are what the server logs show, `g_*` is client-side ground truth. The join key is the `lab/<client>/<seq>` token in the User-Agent.
