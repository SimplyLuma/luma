#!/bin/bash
# go.sh TAG THEME [K=V ...]: one headless Shell run in the oracle container,
# output in $CT_WORK/out-TAG. Runs one Shell at a time.
TAG=$1; TH=$2; shift 2
C=${CT_CONTAINER:-luma-oracle-114}
WORK=${CT_WORK:-/root/luma-shell-oracle/si114/capture-work}
H=/oracle/si114/capture-work/tests/gnome-shell/capture-thumbnail
envs=(-e CT_TAG=$TAG -e CT_THEME=$TH -e CT_OVERLAY=${CT_OVERLAY:-/oracle/si114/capture-work/overlay}
  -e CT_HELPER=${CT_HELPER:-/oracle/si114/capture-work/overlay/data/luma-capture-drag.js})
for kv in "$@"; do envs+=(-e "$kv"); done
podman exec "${envs[@]}" $C python3 /oracle/dock-parity/reaper.py bash $H/run.sh
D=$WORK/out-$TAG
grep -n "JS ERROR\|TypeError\|ReferenceError\|SyntaxError\|Gjs-CRITICAL\|JS WARNING" $D/shell.log | grep -v "dbus\|portal\|Bluetooth\|NetworkManager\|polkit\|geoclue\|upower\|bolt\|malcontent\|ibus\|GDM\|gdm" | head -10
grep "\[ct\]\|\[luma-capture\]" $D/shell.log | cut -c1-700
