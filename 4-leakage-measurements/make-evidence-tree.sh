#!/bin/sh
# Rebuild the original evidence/<name>/ layout that the scripts expect.
# Several studies import code from sibling folders (for example P2-verification
# imports ../P1-meter, P7-caches imports ../P2-residual). In this release each
# study has its own folder, so this script copies the folders into one flat tree.
# Copies, not symlinks: the scripts resolve their own location with Path.resolve().
#
# Usage: ./make-evidence-tree.sh [DEST]      (default DEST: ./_evidence)
# Then:  cd DEST/P1-meter && python3 -B -m unittest
set -eu
here=$(cd "$(dirname "$0")" && pwd)
dest=${1:-$here/_evidence}
mkdir -p "$dest"
copy() { # copy SRC_DIR NAME
  rm -rf "$dest/$2"; cp -R "$here/$1" "$dest/$2"
}
copy A-cache-mechanics A
copy C-server-logs C
for n in P1-meter P1-validation P1-validation-v2 P1-validation-v3 P1-audit-recovery; do copy P1-leakage-meter/$n $n; done
for n in P2-residual P2-calibration-attribution P2-verification; do copy P2-concurrent-load/$n $n; done
for n in P2b-attribution P2b-recovery P2b-verification; do copy P2b-timing-attribution/$n $n; done
copy P7-http-caches P7-caches
for n in P8-telemetry P8-verification; do copy P8-host-telemetry/$n $n; done
for n in P9-content P9-verification; do copy P9-content-hash/$n $n; done
copy S-model-server-gpu-load S-serving
echo "evidence tree written to $dest"
