#!/bin/bash
# One headless Shell run for the notification-icon check. Runs as uid 4242:
# host root's inotify instances are exhausted by other containers.
# GON_TAG, GON_KEYFILE (light|dark|glass), GON_OVERLAY (dir with js/ui) optional.
set -u
TAG=$GON_TAG
OUT=/gon/out/$TAG; rm -rf "$OUT"; mkdir -p "$OUT"; chown 4242:4242 "$OUT"
grep -q '^gon:' /etc/passwd || echo 'gon:x:4242:4242::/tmp/gon-home:/bin/bash' >>/etc/passwd
mkdir -p /run/dbus; [ -S /run/dbus/system_bus_socket ] || { rm -f /run/dbus/pid; dbus-daemon --system --fork; }; rm -rf /run/systemd/seats
exec setpriv --reuid=4242 --regid=4242 --clear-groups env -i PATH=/usr/bin:/bin TAG=$TAG OUT=$OUT \
  KEYFILE=/oracle/notif/keyfile.$GON_KEYFILE OVERLAY=${GON_OVERLAY:-} bash -c '
export XDG_RUNTIME_DIR=/tmp/gon-run-$TAG HOME=/tmp/gon-home-$TAG
rm -rf $XDG_RUNTIME_DIR $HOME; mkdir -m 700 -p $XDG_RUNTIME_DIR
mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications $HOME/.local/share/icons/hicolor/scalable/apps $HOME/.local/share/fonts
cp /oracle/dock-parity/fonts/*.ttf $HOME/.local/share/fonts/ 2>/dev/null
cp $KEYFILE $HOME/.config/glib-2.0/settings/keyfile
cp /gon/apps/*.desktop $HOME/.local/share/applications/
cp /gon/icons/*.svg $HOME/.local/share/icons/hicolor/scalable/apps/
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 ORACLE_OUT=$OUT ORACLE_EVAL=/gon/icons.js ORACLE_SETTLE=8000
[ -n "$OVERLAY" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$OVERLAY/js/ui"
timeout 300 dbus-run-session -- gnome-shell --headless --no-x11 --virtual-monitor 1920x1080 \
  --automation-script=/oracle/probe.js > $OUT/shell.log 2>&1
echo "exit $?"'
