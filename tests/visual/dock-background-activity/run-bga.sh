#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# Headless Shell render of the dock menu's "Run in the Background" row (ADR-033).
# Own runtime, home and output directories so it can run beside other renders.
set -u
tag=bga-dock-$$
export XDG_RUNTIME_DIR=/tmp/$tag-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/$tag-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/luma
cp /oracle/keyfile.${ORACLE_THEME:-light} $HOME/.config/glib-2.0/settings/keyfile
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 ORACLE_OUT=${ORACLE_OUT:?}
export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=/tmp/bga-oracle/overlay/js/ui"
export ORACLE_EVAL=/tmp/bga-oracle/dock-render.js ORACLE_SETTLE=${ORACLE_SETTLE:-9000} ORACLE_STRIP=160
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus
# Start the container's system bus if it is absent, or present but dead.
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then
  rm -f /run/dbus/system_bus_socket /run/dbus/messagebus.pid
  dbus-daemon --system --fork 2>/dev/null
fi
rm -rf /run/systemd/seats
timeout 150 dbus-run-session -- bash -c '
  [ -n "${BGA_NO_SERVICE:-}" ] || python3 /tmp/bga-oracle/fake_background.py > "$ORACLE_OUT/fake.log" 2>&1 &
  sleep 1
  gnome-shell --headless --no-x11 --virtual-monitor ${ORACLE_W:-1920}x${ORACLE_H:-1200} --automation-script=/oracle/probe.js' > "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
