#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Expects this directory at /wm/test inside a Fedora 44 container with the Luma
# Shell, Mutter and mutter-tests RPMs, gtk4 and python3-gobject installed.
# run.sh [extension-source-dir]: run the window memory scenario in a headless Shell
# (WM_SCRIPT=/wm/test/resume-trace.js for the resume and dock geometry trace).
set -u
SRC=${1:-}
if [ -n "$SRC" ]; then
  D=/usr/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com
  rm -rf $D && mkdir -p $D && cp -a "$SRC"/. $D/ && glib-compile-schemas $D/schemas
fi
# A user-mode-only extension enabled before Tiling Shell: at lock GNOME disables
# it and re-enables every later extension, as tiling-toggle does on Luma.
T=/usr/share/gnome-shell/extensions/luma-test-user-only@project-luma.local
mkdir -p $T
printf '{"uuid":"luma-test-user-only@project-luma.local","name":"Test user-only","description":"test","shell-version":["50"]}' > $T/metadata.json
printf 'export default class { enable() {} disable() {} }\n' > $T/extension.js
export XDG_RUNTIME_DIR=/tmp/wm-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/wm-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications
cat > $HOME/.config/glib-2.0/settings/keyfile <<'K'
[org/gnome/shell]
enabled-extensions=['luma-test-user-only@project-luma.local', 'tilingshell@ferrarodomenico.com']
disable-user-extensions=false
welcome-dialog-last-shown-version='999'
K
for a in "org.projectluma.Tide Tide" "org.projectluma.Viola Viola" "org.gnome.Ptyxis Terminal"; do
  set -- $a
  printf '[Desktop Entry]\nType=Application\nName=%s\nExec=python3 /wm/test/app.py %s %s #000\nStartupWMClass=%s\n' "$2" "$1" "$2" "$1" > $HOME/.local/share/applications/$1.desktop
done
# WM_SHELL_OVERLAY: a directory with js/ui files to try Shell changes without a rebuild.
[ -n "${WM_SHELL_OVERLAY:-}" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$WM_SHELL_OVERLAY/js/ui"
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 WM_OUT=${WM_OUT:-/wm/out}
rm -rf "$WM_OUT"; mkdir -p "$WM_OUT"
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
  rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null
fi
rm -rf /run/systemd/seats
timeout 600 dbus-run-session -- gnome-shell --headless --virtual-monitor 1920x1200 --automation-script=${WM_SCRIPT:-/wm/test/wm-test.js} > "$WM_OUT/shell.log" 2>&1
echo "exit $?"
grep -E "\[(wmtest|wmtrace)\] (FAIL|DONE)|JS ERROR|window memory:.*(unavailable|failed)" "$WM_OUT/shell.log" | head -40
# The lock tests need the Shell to keep the desktop work area while locked:
#   patch shelf.js from the installed Shell with shell-overlay/*.diff into
#   <dir>/js/ui/shelf.js and run with WM_SHELL_OVERLAY=<dir>, until the Shell ships it.
