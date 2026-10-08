#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Blur motion frame-time run, inside a Fedora container with the built Mutter
# and Shell RPMs, LumaUI and /oracle mounted. M_TAG M_THEME(light|frost|glass)
# M_CACHE(1|0: LUMA_MUTTER_BLUR_CACHE) M_MONITORS M_SCALE M_SOFTWARE(1|0)
set -u
H=/oracle/blur-motion/harness
T=blurmotion-${M_TAG:-a}
export XDG_RUNTIME_DIR=/tmp/$T-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/$T-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings
theme=${M_THEME:-light}
cat > $HOME/.config/glib-2.0/settings/keyfile <<KEY
[org/project-luma/shell-state]
reduce-transparency=false
surface-treatment='$theme'

[org/gnome/desktop/interface]
color-scheme='default'
enable-animations=true

[org/gnome/mutter]
experimental-features=['scale-monitor-framebuffer']

[org/gnome/shell]
welcome-dialog-last-shown-version='999'
KEY
export GSETTINGS_BACKEND=keyfile LUMA_MUTTER_BLUR_CACHE=${M_CACHE:-1}
if [ "${M_SOFTWARE:-1}" = 1 ]; then export LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1; fi
export MUTTER_DEBUG=${M_DEBUG:-}
export ORACLE_OUT=/oracle/blur-motion/out-${M_TAG:-a} ORACLE_EVAL=$H/eval.js ORACLE_SETTLE=${ORACLE_SETTLE:-6000}
export M_THEME=$theme M_SCALE=${M_SCALE:-1.25}
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; fi
rm -rf /run/systemd/seats
timeout ${M_TIMEOUT:-300} dbus-run-session -- bash -c '
  for n in 1 2 3; do (sleep $((3 + n)); python3 '$H'/blurwin.py org.oracle.Blur$n Blur$n 1500 950 >> "$ORACLE_OUT/win$n.log" 2>&1) & done
  gnome-shell --headless --no-x11 $(IFS=,; for m in ${M_MONITORS:-2880x1800,5120x1440}; do printf -- "--virtual-monitor %s " $m; done) --automation-script=/oracle/probe.js' > "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
