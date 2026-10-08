#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# island-segments.sh OUT: headless Shell with the owner's shell-state
# (owner-shell-state.keyfile), Tiling Shell and its toggle and a pixmap tray
# icon, running island-segments.js. IS_OVERLAY=dir loads dir/js/ui and
# dir/data/theme over the Shell.
set -u
T=$(cd "$(dirname "$0")" && pwd)
export IS_OUT=${1:-/tmp/island-segments}
export XDG_RUNTIME_DIR=/tmp/is-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/is-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings
{ cat $T/owner-shell-state.keyfile; cat <<'K'

[org/gnome/desktop/interface]
color-scheme='prefer-dark'
enable-animations=true

[org/gnome/shell]
enabled-extensions=['tilingshell@ferrarodomenico.com', 'tiling-toggle@project-luma.local']
disable-user-extensions=false
welcome-dialog-last-shown-version='999'
K
} > $HOME/.config/glib-2.0/settings/keyfile
[ -n "${IS_OVERLAY:-}" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$IS_OVERLAY/js/ui:/org/gnome/shell/theme=$IS_OVERLAY/data/theme"
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
rm -rf "$IS_OUT"; mkdir -p "$IS_OUT"
mkdir -p /run/dbus
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 ||
  { rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; }
rm -rf /run/systemd/seats
timeout 300 dbus-run-session -- gnome-shell --headless --no-x11 --force-animations --virtual-monitor 2560x1440 \
  --automation-script=$T/island-segments.js > "$IS_OUT/shell.log" 2>&1
echo "exit $?"
grep -E "\[islandsegments\]" "$IS_OUT/shell.log" | cut -c1-600
grep -c "JS ERROR" "$IS_OUT/shell.log"
