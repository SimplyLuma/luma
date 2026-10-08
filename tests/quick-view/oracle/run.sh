#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Quick View oracle, inside a Fedora 44 container with the Luma Shell, Mutter,
# GTK 3, Filer, luma-shell-state and the candidate luma-quick-view installed.
#   QV_LAYOUT=desk|stacked|hidpi QV_THEME=light|dark QV_OUT=/path run.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
if [ "$(id -u)" = 0 ]; then
  # Filer refuses to run as root: start the system bus, then run as a user.
  mkdir -p /run/dbus
  if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then
    rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null
  fi
  id oracle >/dev/null 2>&1 || useradd -m oracle
  out=${QV_OUT:-$here/out/qv-${QV_LAYOUT:-desk}-${QV_THEME:-dark}}
  mkdir -p "$out"; chown oracle "$out"
  exec runuser -u oracle -- env QV_LAYOUT="${QV_LAYOUT:-desk}" QV_THEME="${QV_THEME:-dark}" QV_OUT="$out" \
    QV_TIMEOUT="${QV_TIMEOUT:-900}" "$0"
fi
layout=${QV_LAYOUT:-desk}; theme=${QV_THEME:-dark}
tag=qv-$layout-$theme
export XDG_RUNTIME_DIR=/tmp/$tag-run; rm -rf "$XDG_RUNTIME_DIR"; mkdir -m 700 -p "$XDG_RUNTIME_DIR"
export HOME=/tmp/$tag-home; rm -rf "$HOME"; mkdir -p "$HOME/.config/glib-2.0/settings"
export QV_OUT=${QV_OUT:-$here/out/$tag} QV_FIXTURES=$HOME/Quick\ View QV_LAYOUT=$layout QV_THEME=$theme
find "$QV_OUT" -mindepth 1 -delete 2>/dev/null; mkdir -p "$QV_OUT"
python3 "$here/fixtures.py" "$QV_FIXTURES"
scheme=default gtk=Luma
[ "$theme" = dark ] && scheme=prefer-dark gtk=Luma-dark
cat > "$HOME/.config/glib-2.0/settings/keyfile" <<KEY
[org/gnome/desktop/interface]
color-scheme='$scheme'
gtk-theme='$gtk'
icon-theme='Prairie'
enable-animations=true

[org/gnome/shell]
welcome-dialog-last-shown-version='999'

[org/gnome/nautilus/preferences]
default-folder-viewer='list-view'
KEY
case $layout in
  hidpi) monitors="--virtual-monitor 2880x1800 --virtual-monitor 1920x1080" ;;
  *) monitors="--virtual-monitor 5120x1440 --virtual-monitor 1920x1200" ;;
esac
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1 NO_AT_BRIDGE=1
timeout ${QV_TIMEOUT:-900} dbus-run-session -- gnome-shell --headless --no-x11 $monitors \
  --automation-script="$here/probe.js" > "$QV_OUT/shell.log" 2>&1
echo "exit $?"
grep -F '[quick-view-oracle] summary' "$QV_OUT/shell.log"
