#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# one.sh PREFIX NAME SCRIPT [VAR=value ...]: one oracle scenario over the
# working overlay (ovl, schemas, ts), like suite.sh PREFIX overlay.
P=$1 name=$2 script=$3; shift 3
EXTRA=(-e SA_OVERLAY=/oracle/shelf-arrange/ovl -e SA_SCHEMA_DIR=/oracle/shelf-arrange/schemas -e SA_TS_TREE=/oracle/shelf-arrange/ts)
envs=()
for e in "$@"; do envs+=(-e "$e"); done
podman exec "${EXTRA[@]}" "${envs[@]}" luma-shell-oracle python3 /oracle/dock-parity/reaper.py \
  bash /oracle/shelf-arrange/tests/run.sh /oracle/shelf-arrange/out/$P-$name $script > /root/luma-shell-oracle/shelf-arrange/out/$P-$name.txt 2>&1
printf '%-12s %s | JS errors %s\n' "$name" \
  "$(grep -o 'DONE [0-9]*/[0-9]* passed' /root/luma-shell-oracle/shelf-arrange/out/$P-$name.txt | tail -2 | paste -sd' ')" \
  "$(grep -o 'JS ERRORS: [0-9]*' /root/luma-shell-oracle/shelf-arrange/out/$P-$name.txt | tail -1)"
grep -a 'FAIL' /root/luma-shell-oracle/shelf-arrange/out/$P-$name.txt | sed 's/.*\[shelfarrange\] //' | cut -c1-400 | head -${FAILS:-15}
