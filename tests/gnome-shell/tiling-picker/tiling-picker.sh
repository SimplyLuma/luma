#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tiling-picker.sh OUT: headless Shell on three monitors with Tiling Shell and
# the Luma tiling toggle, running tiling-picker.js. TP_OVERLAY=dir loads
# dir/extension.js and dir/stylesheet.css over the installed toggle.
set -u
T=$(cd "$(dirname "$0")" && pwd)
export TP_OUT=${1:-/tmp/tiling-picker}
X=/usr/share/gnome-shell/extensions/tiling-toggle@project-luma.local
if [ -n "${TP_OVERLAY:-}" ]; then cp "$TP_OVERLAY"/extension.js "$TP_OVERLAY"/stylesheet.css $X/; fi
export XDG_RUNTIME_DIR=/tmp/tp-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/tp-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings
cat > $HOME/.config/glib-2.0/settings/keyfile <<'K'
[org/project-luma/shell-state]
surface-treatment='light'
shelf-edge='bottom'
shelf-surface-mode='separate'
status-controls-right=true

[org/gnome/desktop/interface]
color-scheme='default'

[org/gnome/shell]
enabled-extensions=['tilingshell@ferrarodomenico.com', 'tiling-toggle@project-luma.local']
disable-user-extensions=false
welcome-dialog-last-shown-version='999'
K
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
rm -rf "$TP_OUT"; mkdir -p "$TP_OUT"
mkdir -p /run/dbus
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 ||
  { rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; }
rm -rf /run/systemd/seats
timeout 240 dbus-run-session -- gnome-shell --headless --no-x11 --virtual-monitor 2560x1440 \
  --virtual-monitor 1920x1200 --virtual-monitor 1600x900 --automation-script=$T/tiling-picker.js > "$TP_OUT/shell.log" 2>&1
echo "exit $?"
grep -E "\[tilingpicker\]" "$TP_OUT/shell.log" | cut -c1-400
grep -c "JS ERROR" "$TP_OUT/shell.log"
