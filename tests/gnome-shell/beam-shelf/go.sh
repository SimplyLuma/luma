#!/bin/bash
# go.sh TAG THEME EDGE SCALE [extra env as K=V ...]
TAG=$1; TH=$2; ED=$3; SC=${4:-1}; shift 4
MON=2560x1440; [ "$SC" = 2 ] && MON=2880x1800
[ "${B_CONTAINER:-luma-shell-oracle}" = luma-shell-oracle ] && while [ "$(bash /root/luma-shell-oracle/dock-parity/busy.sh | wc -l)" -ge 3 ]; do sleep 5; done
envs=(-e B_TAG=$TAG -e B_THEME=$TH -e B_EDGE=$ED -e B_SCALE=$SC -e B_MONITORS=${B_MONITORS:-$MON})
[ -n "${B_OVERLAY_DEFAULT:-}" ] && envs+=(-e "B_OVERLAY=$B_OVERLAY_DEFAULT")
for kv in "$@"; do envs+=(-e "$kv"); done
podman exec "${envs[@]}" ${B_CONTAINER:-luma-shell-oracle} python3 /oracle/dock-parity/reaper.py bash /oracle/beam/harness/run.sh
D=/root/luma-shell-oracle/beam/out-$TAG
grep -n "JS ERROR\|TypeError\|ReferenceError\|SyntaxError\|Gjs-CRITICAL\|JS WARNING" $D/shell.log | grep -v "dbus\|portal\|Bluetooth\|NetworkManager\|polkit\|geoclue\|upower\|bolt\|malcontent\|ibus\|GDM\|gdm" | head -10
grep "\[beam\]" $D/shell.log | cut -c1-900
