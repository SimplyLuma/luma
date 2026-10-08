#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# sticky-dock.sh OUT: headless Shell without Sticky Notes, running
# sticky-dock.js, which installs and removes it. SD_OVERLAY=dir loads
# dir/js/ui over the Shell. Prints the results, the JS error count and any
# Sticky Notes error lines (there must be none).
set -u
T=$(cd "$(dirname "$0")" && pwd)
export SD_OUT=${1:-/tmp/sticky-dock}
export XDG_RUNTIME_DIR=/tmp/sd-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/sd-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications
printf "[org/gnome/shell]\nwelcome-dialog-last-shown-version='999'\n" > $HOME/.config/glib-2.0/settings/keyfile
export XDG_DATA_HOME=$HOME/.local/share
[ -n "${SD_OVERLAY:-}" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$SD_OVERLAY/js/ui"
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1
rm -rf "$SD_OUT"; mkdir -p "$SD_OUT"
mkdir -p /run/dbus
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 ||
  { rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; }
rm -rf /run/systemd/seats
timeout 200 dbus-run-session -- gnome-shell --headless --no-x11 --virtual-monitor 1920x1080 \
  --automation-script=$T/sticky-dock.js > "$SD_OUT/shell.log" 2>&1
echo "exit $?"
grep -E "\[stickydock\]|\[luma-sticky-dock\]" "$SD_OUT/shell.log" | cut -c1-300
grep -c "JS ERROR" "$SD_OUT/shell.log"
grep -i "sticky" "$SD_OUT/shell.log" | grep -iE "error|could not|ServiceUnknown" | grep -v "\[stickydock\]" | head -5
