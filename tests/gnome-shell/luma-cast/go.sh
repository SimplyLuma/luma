#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# go.sh <label> <keyfile> <case> [monitors] [overlay: new|none] [scale]
L=$1; K=$2; C=$3; MON=${4:-2560x1440}; OV=${5:-new}; SC=${6:-1}
while [ -n "$(bash /root/luma-shell-oracle/dock-parity/busy.sh | wc -l | awk '$1>=3')" ]; do sleep 5; done
OVENV=""; [ "$OV" = new ] && OVENV="-e ORACLE_OVERLAY=/oracle/cast-ui/overlay"
podman exec -e CU_TAG=$L -e CU_KEYFILE=$K -e CU_CASE=$C -e CU_MONITORS=$MON -e CU_SCALE=$SC -e CU_MOCK=${CU_MOCK:-1} -e ORACLE_OUT=/oracle/cast-ui/out-$L \
  $OVENV -e ORACLE_EVAL=/oracle/cast-ui/eval.js -e ORACLE_SETTLE=9000 ${CU_EXTRA_ENV:-} \
  luma-shell-oracle python3 /oracle/dock-parity/reaper.py bash /oracle/cast-ui/run.sh
D=/mnt/luma-secondary/luma-cast-ui/shots/$L; rm -rf $D; mkdir -p $D
mv /root/luma-shell-oracle/cast-ui/out-$L/* $D/ && rmdir /root/luma-shell-oracle/cast-ui/out-$L
grep -n "JS ERROR\|JS WARNING\|TypeError\|ReferenceError\|SyntaxError\|Gjs-CRITICAL\|St-CRITICAL" $D/shell.log | grep -v "dbus\|portal\|Bluetooth\|NetworkManager\|polkit\|geoclue\|upower\|bolt\|malcontent\|ibus\|GDM\|gdm" | head -30
grep "\[cu\]" $D/shell.log | cut -c1-400
