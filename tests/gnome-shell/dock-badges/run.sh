#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Run the headless Shell once with the badge overlay and the fakes.
# ORACLE_OUT, ORACLE_EVAL, ORACLE_KEYFILE, ORACLE_W/H, ORACLE_OVERLAY (default /oracle/overlay; "none" for the RPM).
set -u
export XDG_RUNTIME_DIR=/tmp/oracle-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/oracle-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/luma
cp ${ORACLE_KEYFILE:-/oracle/keyfile} $HOME/.config/glib-2.0/settings/keyfile
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1 ORACLE_OUT=${ORACLE_OUT:-/oracle/out}
OV=${ORACLE_OVERLAY:-/oracle/overlay}
[ "$OV" != none ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$OV/js/ui:/org/gnome/shell/theme=$OV/data/theme"
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus; [ -S /run/dbus/system_bus_socket ] || dbus-daemon --system --fork 2>/dev/null; rm -rf /run/systemd/seats
timeout ${ORACLE_TIMEOUT:-150} dbus-run-session -- bash -c '
  python3 /oracle/t/fakes.py > "$ORACLE_OUT/fakes.log" 2>&1 &
  sleep 1
  gnome-shell --headless --no-x11 --force-animations --virtual-monitor ${ORACLE_W:-1920}x${ORACLE_H:-1080} --automation-script=/oracle/probe.js' > "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
