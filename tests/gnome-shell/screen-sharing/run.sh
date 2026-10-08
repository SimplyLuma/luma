#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# One headless Luma session with the real portal stack, then probe.js.
# Inside a Fedora container with the Shell, luma-portal, PipeWire,
# xdg-desktop-portal and GStreamer installed; this directory is /oracle/t.
# ORACLE_OUT, MODE (full|look), SUFFIX, ORACLE_KEYFILE, ORACLE_W, ORACLE_H.
set -u
T=${SHARE_HARNESS:-/oracle/t}
export XDG_RUNTIME_DIR=/tmp/share-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/share-home; rm -rf $HOME
mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications
cp ${ORACLE_KEYFILE:-$T/keyfile.light} $HOME/.config/glib-2.0/settings/keyfile
cp $T/apps/*.desktop $HOME/.local/share/applications/
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
export XDG_CURRENT_DESKTOP=GNOME XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland
export ORACLE_OUT=${ORACLE_OUT:-/oracle/out} SHARE_HARNESS=$T
mkdir -p "$ORACLE_OUT"
# ORACLE_OVERLAY: a directory with js/ui and data/theme loaded over the
# installed Shell, to try a revised module on the built RPMs without a build.
if [ -n "${ORACLE_OVERLAY:-}" ]; then
  export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$ORACLE_OVERLAY/js/ui:/org/gnome/shell/theme=$ORACLE_OVERLAY/data/theme"
fi
mkdir -p /run/dbus; [ -S /run/dbus/system_bus_socket ] || dbus-daemon --system --fork 2>/dev/null
rm -rf /run/systemd/seats
timeout ${ORACLE_TIMEOUT:-420} dbus-run-session -- bash -c '
  pipewire > "$ORACLE_OUT/pipewire.log" 2>&1 &
  sleep 1
  wireplumber > "$ORACLE_OUT/wireplumber.log" 2>&1 &
  sleep 1
  gnome-shell --headless --no-x11 --force-animations \
    --virtual-monitor ${ORACLE_W:-1920}x${ORACLE_H:-1200} \
    --automation-script=$SHARE_HARNESS/probe.js' > "$ORACLE_OUT/shell${SUFFIX:-}.log" 2>&1
echo "exit $?"
