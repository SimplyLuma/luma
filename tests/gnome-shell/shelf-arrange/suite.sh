#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# suite.sh PREFIX [overlay]: every movable-islands oracle scenario, one after
# the other, into /oracle/shelf-arrange/out/PREFIX-*; a summary line each.
# With "overlay", the working tree in /oracle/shelf-arrange/ovl is loaded
# over the installed Shell and the schema in /oracle/shelf-arrange/schemas,
# and the Tiling Shell tree in /oracle/shelf-arrange/ts runs.
P=${1:?prefix}
EXTRA=()
if [ "${2:-}" = overlay ]; then
  EXTRA=(-e SA_OVERLAY=/oracle/shelf-arrange/ovl -e SA_SCHEMA_DIR=/oracle/shelf-arrange/schemas -e SA_TS_TREE=/oracle/shelf-arrange/ts)
fi
T=/oracle/shelf-arrange/tests
run() {
  local name=$1 script=$2; shift 2
  local envs=()
  for e in "$@"; do envs+=(-e "$e"); done
  podman exec "${EXTRA[@]}" "${envs[@]}" luma-shell-oracle python3 /oracle/dock-parity/reaper.py \
    bash $T/run.sh /oracle/shelf-arrange/out/$P-$name $script > /root/luma-shell-oracle/shelf-arrange/out/$P-$name.txt 2>&1
  printf '%-12s %s | JS errors %s\n' "$name" \
    "$(grep -o 'DONE [0-9]*/[0-9]* passed' /root/luma-shell-oracle/shelf-arrange/out/$P-$name.txt | tail -2 | paste -sd' ')" \
    "$(grep -o 'JS ERRORS: [0-9]*' /root/luma-shell-oracle/shelf-arrange/out/$P-$name.txt | tail -1)"
}
run scenes scenes.js
run zones zones.js SA_TIMEOUT=900
run workarea workarea.js
run keyboard keyboard.js SA_RESTART=1 SA_SCRIPT2=restart.js
run multi multi.js SA_MONITORS=2560x1440,1920x1080
run cost cost.js
run modes modes.js SA_TIMEOUT=600
run edges3 edges3.js SA_MONITORS=1920x1200,3440x1440,2560x1440 SA_TIMEOUT=900
run tiling3 tiling3.js SA_MONITORS=1920x1200,3440x1440,2560x1440
run tiling3p tiling3.js SA_MONITORS=1920x1200,3440x1440,2560x1440 SA_PROTRUDE=1
run targeting targeting.js
run weights weights.js
run ghostlive ghostlive.js SA_LIVE=0 SA_MEDIA=0
run family family.js SA_LIVE=0
run notifplace notifplace.js
run asarranged asarranged.js SA_TIMEOUT=500
run round121 round121.js SA_TIMEOUT=1500
run livetile livetile.js SA_TIMEOUT=600
run tiles tiles.js SA_MONITORS=5120x1440,1536x960 SA_TIMEOUT=600
run gaps gaps.js SA_TIMEOUT=600
run hold hold.js SA_TIMEOUT=600
run owner owner.js SA_MONITORS=1920x1200,3440x1440,2560x1440 SA_TIMEOUT=1200
run ownerp owner.js SA_MONITORS=1920x1200,3440x1440,2560x1440 SA_ORDER=3440x1440*,1920x1200,2560x1440 SA_TIMEOUT=1200
run matrix matrix.js SA_TIMEOUT=1500
run retile retile.js SA_MONITORS=1920x1200,3440x1440,2560x1440 SA_TS_TREE=${SA_TS_TREE_PATH-/oracle/shelf-arrange/ts}
run settings settings.js SA_TIMEOUT=600
run menus menus.js
run look look.js
echo SUITE-DONE
