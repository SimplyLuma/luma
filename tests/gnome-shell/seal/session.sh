#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Seal evidence harness: runs as nick inside a logind session (PAMName=seal-session).
# SEAL_TAG SEAL_THEME(light|dark|frost|glass) SEAL_SCALE SEAL_ONLY SEAL_ANIM SEAL_OVERLAY SEAL_MONITOR
set -u
H=/seal/harness
export HOME=/home/nick
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
export SEAL_OUT=/seal/out/${SEAL_TAG:-a}
rm -rf "$SEAL_OUT"; mkdir -p "$SEAL_OUT"
rm -rf $HOME/.config/glib-2.0 $HOME/.local/share/applications $HOME/.local/share/icons
mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications $HOME/.local/share/icons/hicolor/scalable/apps
cp $H/apps/*.desktop $HOME/.local/share/applications/
cp $H/apps/*.svg $HOME/.local/share/icons/hicolor/scalable/apps/
theme=${SEAL_THEME:-light}; scheme=default; [ "$theme" = dark ] && scheme=prefer-dark
cat > $HOME/.config/glib-2.0/settings/keyfile <<KEY
[org/project-luma/shell-state]
surface-treatment='$theme'
reduce-transparency=false

[org/gnome/desktop/interface]
color-scheme='$scheme'
icon-theme='Prairie'
enable-animations=${SEAL_ANIM:-true}
toolkit-accessibility=${SEAL_A11Y:-false}
font-name='Figtree 11'

[org/gnome/mutter]
experimental-features=['scale-monitor-framebuffer']

[org/gnome/shell]
welcome-dialog-last-shown-version='999'
KEY
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
export SEAL_EVAL=$H/eval.js
if [ -n "${SEAL_OVERLAY:-}" ]; then
  export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$SEAL_OVERLAY/js/ui:/org/gnome/shell/theme=$SEAL_OVERLAY/data/theme"
fi
update-desktop-database $HOME/.local/share/applications 2>/dev/null
gtk-update-icon-cache -f -t $HOME/.local/share/icons/hicolor 2>/dev/null
if [ "${SEAL_A11Y:-false}" = true ]; then (sleep 6; python3 $H/atspi.py > "$SEAL_OUT/atspi.log" 2>&1) & fi
exec timeout ${SEAL_TIMEOUT:-300} gnome-shell --headless --no-x11 --virtual-monitor ${SEAL_MONITOR:-1920x1200} \
  --automation-script=$H/probe.js > "$SEAL_OUT/shell.log" 2>&1
