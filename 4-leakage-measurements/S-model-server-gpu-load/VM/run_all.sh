#!/bin/bash
# S1 sequence: cache_on calibration -> freeze -> holdout -> utility; then cache_off the same.
set -eux
cd ~/s1
PY=~/venv/bin/python
$PY trials.py run --phase cache_on --split calibration
$PY trials.py freeze --phase cache_on
$PY trials.py run --phase cache_on --split holdout
$PY utility.py --phase cache_on
~/s1/serve.sh cache_off
$PY trials.py run --phase cache_off --split calibration
$PY trials.py freeze --phase cache_off
$PY trials.py run --phase cache_off --split holdout
$PY utility.py --phase cache_off
echo S1_DONE
