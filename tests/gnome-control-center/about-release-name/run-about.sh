#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Render Settings › About and System Details in Xvfb. $1: {light,dark}-{1x,2x}
set -u
TAG=$1; OUT=${OUT:-/work/out}; mkdir -p "$OUT"
export HOME=/tmp/about-home-$TAG XDG_RUNTIME_DIR=/tmp/about-run-$TAG
rm -rf "$HOME" "$XDG_RUNTIME_DIR"; mkdir -p "$HOME" "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export XDG_CURRENT_DESKTOP=GNOME GDK_BACKEND=x11 GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 GTK_A11Y=atspi
case "$TAG" in *dark*) export ADW_DEBUG_COLOR_SCHEME=prefer-dark ;; *) export ADW_DEBUG_COLOR_SCHEME=prefer-light ;; esac
W=1000; H=1000
case "$TAG" in *2x) export GDK_SCALE=2; W=2000; H=2000 ;; esac
Xvfb :77 -screen 0 ${W}x${H}x24 >/dev/null 2>&1 & XVFB=$!
export DISPLAY=:77; sleep 2
export OUT TAG
dbus-run-session -- bash -c '
  /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
  sleep 1
  G_DEBUG=fatal-criticals-off gnome-control-center system about > "$OUT/$TAG-settings.log" 2>&1 &
  python3 /work/drive_about.py "$OUT" "$TAG" > "$OUT/$TAG-drive.log" 2>&1
  pkill -f "^gnome-control-center" || true
'
kill $XVFB 2>/dev/null; sleep 1
echo "== $TAG"; cat "$OUT/$TAG-drive.log"
