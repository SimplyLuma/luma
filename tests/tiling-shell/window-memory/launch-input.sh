#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# launch-input.sh OUT: run launch-input.js in a headless Shell inside a Fedora
# container that has the Shell, Mutter, libadwaita and Tiling Shell under test.
# This directory is /wm/test.
set -u
export WM_OUT=${1:-/wm/out}
export XDG_RUNTIME_DIR=/tmp/li-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/li-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings
cat > $HOME/.config/glib-2.0/settings/keyfile <<'K'
[org/gnome/shell]
enabled-extensions=['tilingshell@ferrarodomenico.com']
disable-user-extensions=false
welcome-dialog-last-shown-version='999'

[org/gnome/desktop/interface]
enable-animations=true
K
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 WM_APP=/wm/test/app.py
rm -rf "$WM_OUT"; mkdir -p "$WM_OUT"
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
  rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null
fi
rm -rf /run/systemd/seats
timeout 300 dbus-run-session -- gnome-shell --headless --no-x11 --force-animations --virtual-monitor 1920x1200 --automation-script=/wm/test/launch-input.js > "$WM_OUT/shell.log" 2>&1
echo "exit $?"
grep -E "\[launchinput\]|JS ERROR" "$WM_OUT/shell.log" | cut -c1-400
