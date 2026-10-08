#!/bin/bash
# Capture thumbnail oracle run, inside a container with /oracle mounted and the
# built Shell installed. The changed JS and CSS load from a resource overlay
# (CT_OVERLAY, a directory with js/ui and data/theme; `none` tests the
# installed packages) and the drag helper from CT_HELPER.
# CT_TAG CT_THEME(light|dark|frost|glass) CT_ONLY CT_EVAL CT_TIMEOUT
set -u
H=$(cd "$(dirname "$0")" && pwd)
export CT_HARNESS=$H CT_CAPTURE_HARNESS=${CT_CAPTURE_HARNESS:-$(dirname "$H")/capture}
T=ct-${CT_TAG:-a}
export XDG_RUNTIME_DIR=/tmp/$T-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/$T-home
rm -rf $HOME
mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/fonts $HOME/.local/share/applications \
  $HOME/Pictures $HOME/Videos $HOME/Desktop
cp /oracle/dock-parity/fonts/*.ttf $HOME/.local/share/fonts/
printf 'XDG_DESKTOP_DIR="$HOME/Desktop"\nXDG_PICTURES_DIR="$HOME/Pictures"\nXDG_VIDEOS_DIR="$HOME/Videos"\n' > $HOME/.config/user-dirs.dirs
theme=${CT_THEME:-light}; scheme=default; [ "$theme" = dark ] && scheme=prefer-dark
cat > $HOME/.config/glib-2.0/settings/keyfile <<KEY
[org/project-luma/shell-state]
reduce-transparency=false
shelf-edge='bottom'
shelf-edge-mode='floating'
shelf-float-ends=true
shelf-padding=8
shelf-span-full=false
shelf-surface-mode='separate'
status-controls-right=true
surface-treatment='$theme'

[org/gnome/desktop/interface]
color-scheme='$scheme'
enable-animations=true

[org/gnome/shell]
favorite-apps=['org.oracle.Claude.desktop']
welcome-dialog-last-shown-version='999'
KEY
printf '[Desktop Entry]\nType=Application\nName=Claude\nExec=python3 /oracle/live-island/harness/win.py org.oracle.Claude Claude\nIcon=org.gnome.Settings\n' > $HOME/.local/share/applications/org.oracle.Claude.desktop
# Two image handlers that record their argv instead of showing anything: the
# default one (Opener) and a second one for Open With (Viewer).
for n in Opener Viewer; do
  printf '[Desktop Entry]\nType=Application\nName=%s\nExec=%s/handler.sh %s %%u\nMimeType=image/png;video/webm;\nIcon=org.gnome.Settings\n' $n $H $n > $HOME/.local/share/applications/oracle-$n.desktop
done
printf '[Default Applications]\nimage/png=oracle-Opener.desktop\nvideo/webm=oracle-Opener.desktop\n[Added Associations]\nimage/png=oracle-Opener.desktop;oracle-Viewer.desktop;\n' > $HOME/.config/mimeapps.list
unset GSETTINGS_SCHEMA_DIR
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
export LUMA_CAPTURE_DRAG_HELPER=${CT_HELPER:-/usr/libexec/luma-capture-drag}
export ORACLE_OUT=${CT_OUT:-/oracle/si114/capture-work/out-${CT_TAG:-a}} ORACLE_EVAL=${CT_EVAL:-$H/eval.js} ORACLE_SETTLE=${ORACLE_SETTLE:-9000}
if [ "${CT_OVERLAY:-none}" != none ]; then
  export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$CT_OVERLAY/js/ui:/org/gnome/shell/theme=$CT_OVERLAY/data/theme"
fi
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then
  rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; fi
rm -rf /run/systemd/seats
update-desktop-database $HOME/.local/share/applications 2>/dev/null
timeout ${CT_TIMEOUT:-300} dbus-run-session -- bash -c '
  python3 $CT_HARNESS/fakebus.py > "$ORACLE_OUT/fakebus.log" 2>&1 &
  (sleep 4; python3 /oracle/live-island/harness/win.py org.oracle.Claude Claude >> "$ORACLE_OUT/win.log" 2>&1) &
  nice gnome-shell --headless --no-x11 --virtual-monitor 2560x1440 --automation-script=/oracle/probe.js' >> "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
