#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# live-modes.sh OUT: headless Shell with a Live Extension and a playing tab,
# switched through every appearance mode (live-modes.js). This directory is $T.
set -u
T=$(cd "$(dirname "$0")" && pwd)
export LM_OUT=${1:-/tmp/live-modes}
export XDG_RUNTIME_DIR=/tmp/lm-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/lm-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings
cat > $HOME/.config/glib-2.0/settings/keyfile <<'K'
[org/project-luma/shell-state]
surface-treatment='dark'
shelf-edge='bottom'
shelf-surface-mode='separate'
status-controls-right=true
shelf-material='dark'
shelf-group-layout='centered-combined'
shelf-islands=['dock', 'actions']

[org/gnome/desktop/interface]
color-scheme='prefer-dark'
enable-animations=true

[org/gnome/shell]
welcome-dialog-last-shown-version='999'
K
[ -n "${LM_OVERLAY:-}" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$LM_OVERLAY/js/ui:/org/gnome/shell/theme=$LM_OVERLAY/data/theme"
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
rm -rf "$LM_OUT"; mkdir -p "$LM_OUT"
mkdir -p /run/dbus
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 ||
  { rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; }
rm -rf /run/systemd/seats
timeout 240 dbus-run-session -- bash -c "
  python3 $T/broker.py > $LM_OUT/broker.log 2>&1 &
  (sleep 2; python3 $T/chrome-mpris.py > $LM_OUT/mpris.log 2>&1) &
  gnome-shell --headless --no-x11 --force-animations --virtual-monitor 2560x1440 --automation-script=$T/live-modes.js" > "$LM_OUT/shell.log" 2>&1
echo "exit $?"
grep -E "\[livemodes\]" "$LM_OUT/shell.log" | cut -c1-330
grep -c "JS ERROR" "$LM_OUT/shell.log"
grep "not in the stage" "$LM_OUT/shell.log" | cut -c1-200 | head -3
