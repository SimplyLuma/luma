#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Render Settings > Displays > Night Light in Xvfb with a brand-new $HOME (no
# dconf user value for night-light-temperature exists), so the slider's start
# position is whatever Luma's shipped package default resolves to.
set -u
OUT=${OUT:-/work/out}; mkdir -p "$OUT"
TAG=${1:-night-light}
export HOME=/tmp/night-light-home-$TAG XDG_RUNTIME_DIR=/tmp/night-light-run-$TAG
rm -rf "$HOME" "$XDG_RUNTIME_DIR"; mkdir -p "$HOME" "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export XDG_CURRENT_DESKTOP=GNOME GDK_BACKEND=x11 GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 GTK_A11Y=atspi
DISPLAY_NUM=$((70 + RANDOM % 20))
Xvfb :$DISPLAY_NUM -screen 0 1100x860x24 >/dev/null 2>&1 &
XVFB=$!
export DISPLAY=:$DISPLAY_NUM
sleep 2
export OUT TAG
dbus-run-session -- bash -c '
  /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
  sleep 1
  G_DEBUG=fatal-criticals-off gnome-control-center display > "$OUT/$TAG-settings.log" 2>&1 &
  python3 /work/drive_night_light.py "$OUT" "$TAG"
  status=$?
  pkill -f "^gnome-control-center" || true
  exit $status
'
status=$?
kill $XVFB 2>/dev/null
echo "done $TAG (exit $status)"
exit $status
