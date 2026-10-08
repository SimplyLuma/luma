#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# run.sh OUT SCRIPT: a headless Shell with the owner's shell-state
# (owner-shell-state.keyfile) plus SA_KEYFILE (extra keyfile lines), Tiling
# Shell and its toggle, running SCRIPT as the automation script.
#   SA_OVERLAY=dir     load dir/js/ui and dir/data/theme over the Shell
#   SA_MONITORS=WxH[,WxH]  virtual monitors (default 2560x1440)
#   SA_RESTART=1       run the Shell twice with the same settings, the second
#                      time with SCRIPT2 (persistence across a restart)
set -u
T=$(cd "$(dirname "$0")" && pwd)
export SA_OUT=${1:?out dir}
SCRIPT=${2:?script}
export XDG_RUNTIME_DIR=/tmp/sa-run-$$; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/sa-home-$$; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/fonts \
  $HOME/.local/share/applications $HOME/.local/share/icons/hicolor/scalable/apps
# Figtree and the Luma app launchers the dock shows (SA_APPS, SA_FONTS).
cp ${SA_FONTS:-/oracle/dock-parity/fonts}/*.ttf $HOME/.local/share/fonts/ 2>/dev/null
for f in ${SA_APPS:-/oracle/apps}/*.desktop; do cp "$f" $HOME/.local/share/applications/; done 2>/dev/null
cp ${SA_APPS:-/oracle/apps}/*.svg $HOME/.local/share/icons/hicolor/scalable/apps/ 2>/dev/null
printf '[Desktop Entry]\nType=Application\nName=Files\nExec=python3 %s/win.py org.oracle.Files Files\nIcon=org.projectluma.Notes\n' "$T" > $HOME/.local/share/applications/org.oracle.Files.desktop
{ cat $T/owner-shell-state.keyfile; printf '%s\n' "${SA_KEYFILE:-}"; cat <<'K'

[org/gnome/desktop/interface]
color-scheme='prefer-dark'
enable-animations=true

[org/gnome/shell]
favorite-apps=['org.oracle.Files.desktop', 'org.projectluma.Messages.desktop', 'org.projectluma.Calendar.desktop', 'org.projectluma.Notes.desktop', 'org.projectluma.Tide.desktop', 'org.projectluma.Photos.desktop', 'org.projectluma.Weather.desktop']
enabled-extensions=['tilingshell@ferrarodomenico.com', 'tiling-toggle@project-luma.local']
disable-user-extensions=false
welcome-dialog-last-shown-version='999'
K
} > $HOME/.config/glib-2.0/settings/keyfile
# SA_TS_TREE=dir: a Tiling Shell tree to run instead of the installed one.
if [ -n "${SA_TS_TREE:-}" ]; then
  mkdir -p $HOME/.local/share/gnome-shell/extensions
  cp -r "$SA_TS_TREE" $HOME/.local/share/gnome-shell/extensions/tilingshell@ferrarodomenico.com
fi
[ -n "${SA_OVERLAY:-}" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$SA_OVERLAY/js/ui:/org/gnome/shell/theme=$SA_OVERLAY/data/theme"
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
[ -n "${SA_SCHEMA_DIR:-}" ] && export GSETTINGS_SCHEMA_DIR=$SA_SCHEMA_DIR
rm -rf "$SA_OUT"; mkdir -p "$SA_OUT"
mkdir -p /run/dbus
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1 ||
  { rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; }
rm -rf /run/systemd/seats
MON=()
IFS=, read -ra SIZES <<< "${SA_MONITORS:-2560x1440}"
for m in "${SIZES[@]}"; do MON+=(--virtual-monitor "$m"); done
run() {
  timeout ${SA_TIMEOUT:-400} dbus-run-session -- bash -c "
    [ \"\${SA_FIXTURES:-1}\" = 1 ] && python3 $T/fixtures.py > $SA_OUT/fixtures.log 2>&1 &
    gnome-shell --headless --no-x11 --force-animations ${MON[*]} --automation-script=$1" > "$SA_OUT/$2" 2>&1
  echo "exit $? ($2)"
}
run $T/$SCRIPT shell.log
[ -n "${SA_RESTART:-}" ] && run $T/${SA_SCRIPT2:?} shell-restart.log
grep -h -E "\[shelfarrange\]" "$SA_OUT"/shell*.log | cut -c1-400
echo "JS ERRORS: $(cat "$SA_OUT"/shell*.log | grep -c "JS ERROR")"
grep -h -A3 'JS ERROR' "$SA_OUT"/shell*.log | head -30
