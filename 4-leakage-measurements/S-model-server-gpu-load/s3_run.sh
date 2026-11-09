#!/bin/bash
# S3 sequence on the running cache_on server; salted gateway 9002 logs to gw-s3-salted.jsonl.
set -eux
cd ~/s1
PY=~/venv/bin/python
$PY trials_s3.py run --split calibration
$PY trials_s3.py freeze
$PY trials_s3.py run --split holdout
echo S3_DONE
