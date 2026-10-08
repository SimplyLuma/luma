#!/bin/bash
set -u
T=repro-${R_TAG}
export XDG_RUNTIME_DIR=/tmp/$T-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/$T-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/applications
cat > $HOME/.config/glib-2.0/settings/keyfile <<KEY
[org/project-luma/shell-state]
shelf-edge='bottom'
shelf-edge-mode='floating'
shelf-float-ends=false
shelf-padding=10
shelf-span-full=false
shelf-surface-mode='separate'
shelf-material='dark'
status-controls-right=true
surface-treatment='dark'

[org/gnome/desktop/interface]
color-scheme='prefer-dark'
icon-theme='Prairie'

[org/gnome/shell]
favorite-apps=[${R_FAVS:-'org.gnome.Settings.desktop', 'org.gnome.Nautilus.desktop', 'org.oracle.Claude.desktop'}]
welcome-dialog-last-shown-version='999'
enabled-extensions=[${R_EXT:-}]
KEY
printf '[Desktop Entry]\nType=Application\nName=Claude\nExec=python3 /oracle/live-island/harness/win.py org.oracle.Claude Claude\nIcon=org.gnome.Settings\n' > $HOME/.local/share/applications/org.oracle.Claude.desktop
for i in $(seq -w 1 ${R_NFAV:-0}); do printf '[Desktop Entry]\nType=Application\nName=Fav %s\nExec=true\nIcon=org.gnome.Settings\n' $i > $HOME/.local/share/applications/org.oracle.Fav$i.desktop; done
printf '[Desktop Entry]\nType=Application\nName=Second\nExec=python3 /oracle/live-island/harness/win.py org.oracle.Second Second\nIcon=org.gnome.Nautilus\n' > $HOME/.local/share/applications/org.oracle.Second.desktop
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1
export ORACLE_OUT=/beam/repro/out-${R_TAG} ORACLE_EVAL=/beam/repro/eval.js ORACLE_SETTLE=9000
[ -n "${R_OVERLAY:-}" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$R_OVERLAY/js/ui"
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
rm -rf /run/systemd/seats; mkdir -p /run/dbus
dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1 || { rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork; }
timeout 150 dbus-run-session -- bash -c '
  (sleep 4; python3 /oracle/live-island/harness/win.py org.oracle.Claude Claude > $ORACLE_OUT/win.log 2>&1) &
  (sleep 5; python3 /oracle/live-island/harness/win.py org.oracle.Second Second >> $ORACLE_OUT/win.log 2>&1) &
  gnome-shell --headless --no-x11 ${R_MONITORS} --automation-script=/oracle/probe.js' > "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
