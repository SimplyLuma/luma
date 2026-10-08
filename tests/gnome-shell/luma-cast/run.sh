#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Luma Cast UI oracle run. CU_TAG, CU_KEYFILE (dark|light), CU_CASE, CU_SCALE, CU_MONITORS, ORACLE_OVERLAY, ORACLE_OUT
set -u
T=${CU_TAG:-a}
export XDG_RUNTIME_DIR=/tmp/luma-cast-ui-run-$T; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/luma-cast-ui-home-$T; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/fonts $HOME/.local/share/applications
cp /oracle/dock-parity/fonts/*.ttf $HOME/.local/share/fonts/
cp /oracle/cast-ui/keyfile.${CU_KEYFILE:-dark} $HOME/.config/glib-2.0/settings/keyfile
for spec in Slides:Viewer Mail:Messages Notes:Notes Browser:Maps; do
  n=${spec%%:*}; i=${spec##*:}
  printf '[Desktop Entry]\nType=Application\nName=%s\nExec=python3 /oracle/cast-ui/win.py org.oracle.%s %s\nIcon=org.projectluma.%s\n' $n $n $n $i > $HOME/.local/share/applications/org.oracle.$n.desktop
done
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1 ORACLE_OUT=${ORACLE_OUT:-/oracle/cast-ui/out}
if [ -n "${ORACLE_OVERLAY:-}" ]; then
  export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$ORACLE_OVERLAY/js/ui:/org/gnome/shell/theme=$ORACLE_OVERLAY/data/theme"
  if [ -d "$ORACLE_OVERLAY/data/icons" ]; then mkdir -p $HOME/.local/share/icons/hicolor/scalable/status; cp "$ORACLE_OVERLAY"/data/icons/hicolor/scalable/status/*.svg $HOME/.local/share/icons/hicolor/scalable/status/ 2>/dev/null; fi
fi
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; fi
rm -rf /run/systemd/seats
MON=""
IFS=, read -ra M <<< "${CU_MONITORS:-2560x1440}"
for m in "${M[@]}"; do MON="$MON --virtual-monitor $m"; done
export MON
timeout ${CU_TIMEOUT:-300} dbus-run-session -- bash -c '
  if [ -n "${CU_MOCK:-}" ]; then python3 /oracle/cast-ui/mock_cast.py > "$ORACLE_OUT/mock.log" 2>&1 & fi
  gnome-shell --headless --no-x11 $MON --automation-script=/oracle/probe.js' > "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
