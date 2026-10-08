#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Render Settings › Mouse & Touchpad › TrackPoint in Xvfb with a faked
# ThinkPad touchpad and TrackPoint (umockdev). $1: light or dark.
set -u
TAG=$1; OUT=${OUT:-/work/out}; mkdir -p "$OUT"
export HOME=/tmp/tp-home-$TAG XDG_RUNTIME_DIR=/tmp/tp-run-$TAG
rm -rf "$HOME" "$XDG_RUNTIME_DIR"; mkdir -p "$HOME" "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export XDG_CURRENT_DESKTOP=GNOME GDK_BACKEND=x11 GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 GTK_A11Y=atspi
case "$TAG" in dark) export ADW_DEBUG_COLOR_SCHEME=prefer-dark ;; *) export ADW_DEBUG_COLOR_SCHEME=prefer-light ;; esac
Xvfb :78 -screen 0 1100x800x24 >/dev/null 2>&1 & XVFB=$!
export DISPLAY=:78; sleep 2
export OUT TAG
dbus-run-session -- bash -c '
  /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
  sleep 1
  umockdev-run --device /work/devices.umockdev -- gnome-control-center mouse > "$OUT/$TAG-settings.log" 2>&1 &
  python3 /work/drive.py "$OUT" "$TAG" > "$OUT/$TAG-drive.log" 2>&1
  echo "drive exit $?" >> "$OUT/$TAG-drive.log"
  pkill -f "^gnome-control-center" || true
'
kill $XVFB 2>/dev/null; sleep 1
# Trim to the window: everything that is not the Xvfb black background.
magick "$OUT/$TAG-full.png" -trim +repage "$OUT/$TAG.png" 2>/dev/null || cp "$OUT/$TAG-full.png" "$OUT/$TAG.png"
echo "== $TAG"; cat "$OUT/$TAG-drive.log"
