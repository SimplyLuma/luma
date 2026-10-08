#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Render Settings with the Luma toolkit in Xvfb, against the mock
# org.projectluma.Cast1 service.
# $1: label (dark-* or light-*); remaining arguments: gnome-control-center
# arguments (default: display cast).
set -u
TAG=${1:-dark-cast}; shift || true
ARGS=("$@"); [ ${#ARGS[@]} -eq 0 ] && ARGS=(display cast)
OUT=${OUT:-/work/out}; mkdir -p "$OUT"
export HOME=/tmp/cast-settings-home-$TAG XDG_RUNTIME_DIR=/tmp/cast-settings-run-$TAG
rm -rf "$HOME" "$XDG_RUNTIME_DIR"; mkdir -p "$HOME" "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export XDG_CURRENT_DESKTOP=GNOME GDK_BACKEND=x11 GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 GTK_A11Y=atspi
case "$TAG" in
  dark*) export ADW_DEBUG_COLOR_SCHEME=prefer-dark ;;
  *) export ADW_DEBUG_COLOR_SCHEME=prefer-light ;;
esac
export GSETTINGS_SCHEMA_DIR=/work/schemas
DISPLAY_NUM=$((70 + RANDOM % 20))
Xvfb :$DISPLAY_NUM -screen 0 1100x860x24 >/dev/null 2>&1 &
XVFB=$!
export DISPLAY=:$DISPLAY_NUM
sleep 2
export OUT TAG
export CC_ARGS="${ARGS[*]}"
dbus-run-session -- bash -c '
  /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
  sleep 1
  python3 /work/mock/mock_cast.py > "$OUT/$TAG-mock.log" 2>&1 &
  sleep 1
  G_DEBUG=fatal-criticals-off gnome-control-center $CC_ARGS > "$OUT/$TAG-settings.log" 2>&1 &
  python3 /work/drive_cast.py "$OUT" "$TAG" > "$OUT/$TAG-drive.log" 2>&1
  pkill -f "^gnome-control-center" || true
'
kill $XVFB 2>/dev/null
echo "done $TAG ($CC_ARGS)"
