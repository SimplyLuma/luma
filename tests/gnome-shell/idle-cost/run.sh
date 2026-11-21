#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# One headless Luma session at rest, then probe.js. Inside a Fedora (or Luma
# OS tree) container with the Shell installed; this directory is /oracle/t.
# ORACLE_OUT, MODE (check|find), ORACLE_MONITORS (default three: 1920x1200,
# 2560x1440, 1920x1200), ORACLE_SCALES (default 1,1,1.25), IDLE_MS,
# ORACLE_OVERLAY (a directory with js/ui and data/theme loaded over the Shell).
set -u
T=${IDLE_HARNESS:-/oracle/t}
export XDG_RUNTIME_DIR=/tmp/idle-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/idle-home; rm -rf $HOME
mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications
# The OS defaults (dconf luma.d, same group/key syntax as the keyfile
# backend) under the harness's own keys, as a fresh Luma account has them.
{ cat /etc/dconf/db/luma.d/* 2>/dev/null; cat ${ORACLE_KEYFILE:-$T/keyfile}; } \
  > $HOME/.config/glib-2.0/settings/keyfile
cp $T/apps/*.desktop $HOME/.local/share/applications/
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
export ORACLE_OUT=${ORACLE_OUT:-/oracle/out} IDLE_HARNESS=$T
export ORACLE_SCALES=${ORACLE_SCALES-1,1,1.25}
mkdir -p "$ORACLE_OUT"
if [ -n "${ORACLE_OVERLAY:-}" ]; then
  export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$ORACLE_OVERLAY/js/ui:/org/gnome/shell/theme=$ORACLE_OVERLAY/data/theme"
fi
mkdir -p /run/dbus; [ -S /run/dbus/system_bus_socket ] || dbus-daemon --system --fork 2>/dev/null
rm -rf /run/systemd/seats
monitors=${ORACLE_MONITORS-1920x1200 2560x1440 1920x1200}
timeout ${ORACLE_TIMEOUT:-300} dbus-run-session -- \
  gnome-shell --headless --no-x11 --force-animations \
    $(for m in $monitors; do printf -- "--virtual-monitor %s " $m; done) \
    --automation-script=$T/probe.js > "$ORACLE_OUT/shell${SUFFIX:-}.log" 2>&1
echo "exit $?"
