#!/bin/bash
# go.sh TAG THEME EDGE SCALE [K=V ...]: one headless Shell run; output in /root/luma-shell-oracle/capture/out-TAG
TAG=$1; TH=$2; ED=$3; SC=${4:-1}; shift 4
MON=2560x1440; [ "$SC" = 2 ] && MON=2880x1800
C=${C_CONTAINER:-luma-shell-oracle}
[ "$C" = luma-shell-oracle ] && while [ "$(bash /root/luma-shell-oracle/dock-parity/busy.sh | wc -l)" -ge 3 ]; do sleep 5; done
envs=(-e C_TAG=$TAG -e C_THEME=$TH -e C_EDGE=$ED -e C_SCALE=$SC -e C_MONITORS=${C_MONITORS:-$MON})
# The packaged build's contents instead of the working overlay, for a whole matrix.
for v in C_OVERLAY C_SCHEMAS C_ICONS C_X11 C_DROP_TARGETS C_DRAG_HELPER C_DEBUG; do [ -n "${!v:-}" ] && envs+=(-e "$v=${!v}"); done
for kv in "$@"; do envs+=(-e "$kv"); done
podman exec "${envs[@]}" $C python3 /oracle/dock-parity/reaper.py bash /oracle/capture/harness/run.sh
D=/root/luma-shell-oracle/capture/out-$TAG
grep -n "JS ERROR\|TypeError\|ReferenceError\|SyntaxError\|Gjs-CRITICAL\|JS WARNING" $D/shell.log | grep -v "dbus\|portal\|Bluetooth\|NetworkManager\|polkit\|geoclue\|upower\|bolt\|malcontent\|ibus\|GDM\|gdm" | head -10
grep "\[capture\]\|Luma Capture" $D/shell.log | cut -c1-600
