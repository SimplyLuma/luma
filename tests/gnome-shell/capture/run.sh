#!/bin/bash
# Capture oracle run, inside a container with /oracle mounted (the shared
# luma-shell-oracle with an overlay, or a Fedora container with the built RPMs
# and C_OVERLAY=none). C_TAG C_THEME(light|dark|frost|glass) C_EDGE C_SCALE
# C_ONLY C_MONITORS C_ANIM C_KEEP_HOME C_OVERLAY C_A11Y
set -u
T=capture-${C_TAG:-a}
export XDG_RUNTIME_DIR=/tmp/$T-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
# C_HOME_TAG reuses another run's home (with C_KEEP_HOME, a second session).
export HOME=/tmp/capture-${C_HOME_TAG:-${C_TAG:-a}}-home
[ -n "${C_KEEP_HOME:-}" ] || rm -rf $HOME
mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/fonts $HOME/.local/share/applications \
  $HOME/.local/share/icons/hicolor/scalable/status $HOME/Pictures $HOME/Videos $HOME/Desktop $HOME/Elsewhere
cp /oracle/dock-parity/fonts/*.ttf $HOME/.local/share/fonts/
[ "${C_OVERLAY:-}" != none ] && cp ${C_ICONS:-/oracle/capture/icons}/*.svg $HOME/.local/share/icons/hicolor/scalable/status/
printf 'XDG_DESKTOP_DIR="$HOME/Desktop"\nXDG_PICTURES_DIR="$HOME/Pictures"\nXDG_VIDEOS_DIR="$HOME/Videos"\n' > $HOME/.config/user-dirs.dirs
theme=${C_THEME:-light}; scheme=default; [ "$theme" = dark ] && scheme=prefer-dark
if [ -z "${C_KEEP_HOME:-}" ]; then
cat > $HOME/.config/glib-2.0/settings/keyfile <<KEY
[org/project-luma/shell-state]
reduce-transparency=false
shelf-edge='${C_EDGE:-bottom}'
shelf-edge-mode='floating'
shelf-float-ends=true
shelf-padding=8
shelf-span-full=false
shelf-surface-mode='separate'
status-controls-right=true
surface-treatment='$theme'

[org/gnome/desktop/interface]
color-scheme='$scheme'
enable-animations=${C_ANIM:-true}
toolkit-accessibility=${C_A11Y:-false}

[org/gnome/mutter]
experimental-features=['scale-monitor-framebuffer']

[org/gnome/shell]
favorite-apps=['org.oracle.Claude.desktop']
welcome-dialog-last-shown-version='999'
KEY
fi
for app in Claude Second; do
  printf '[Desktop Entry]\nType=Application\nName=%s\nExec=python3 /oracle/live-island/harness/win.py org.oracle.%s %s\nIcon=org.gnome.Settings\n' $app $app $app > $HOME/.local/share/applications/org.oracle.$app.desktop
done
# Opening a capture from its thumbnail is recorded instead of launching a viewer.
printf '[Desktop Entry]\nType=Application\nName=Opener\nExec=/oracle/capture/harness/opener.sh %%u\nMimeType=image/png;video/webm;video/mp4;\nNoDisplay=true\n' > $HOME/.local/share/applications/oracle-opener.desktop
printf '[Default Applications]\nimage/png=oracle-opener.desktop\nvideo/webm=oracle-opener.desktop\nvideo/mp4=oracle-opener.desktop\n' > $HOME/.config/mimeapps.list
export GSETTINGS_SCHEMA_DIR=${C_SCHEMAS:-/oracle/capture/schemas}
[ "${C_OVERLAY:-}" = none ] && unset GSETTINGS_SCHEMA_DIR
[ -n "${C_DRAG_HELPER:-}" ] && export LUMA_CAPTURE_DRAG_HELPER=$C_DRAG_HELPER LUMA_CAPTURE_DEBUG=${C_DEBUG:-1}
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
export ORACLE_OUT=/oracle/capture/out-${C_TAG:-a} ORACLE_EVAL=${C_EVAL:-/oracle/capture/harness/eval.js} ORACLE_SETTLE=${ORACLE_SETTLE:-9000}
[ "${C_OVERLAY:-}" != none ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=${C_OVERLAY:-/oracle/capture/overlay}/js/ui:/org/gnome/shell/theme=${C_OVERLAY:-/oracle/capture/overlay}/data/theme"
[ -n "${C_KEEP_HOME:-}" ] || rm -rf "$ORACLE_OUT"
mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; fi
rm -rf /run/systemd/seats
update-desktop-database $HOME/.local/share/applications 2>/dev/null
timeout ${C_TIMEOUT:-240} dbus-run-session -- bash -c '
  pipewire > "$ORACLE_OUT/pipewire.log" 2>&1 & sleep 1; wireplumber > "$ORACLE_OUT/wireplumber.log" 2>&1 &
  (sleep 4; python3 /oracle/live-island/harness/win.py org.oracle.Claude Claude >> "$ORACLE_OUT/win.log" 2>&1) &
  (sleep 5; python3 /oracle/live-island/harness/win.py org.oracle.Second Second >> "$ORACLE_OUT/win2.log" 2>&1) &
  if [ "${C_A11Y:-false}" = true ]; then (sleep 7; python3 /oracle/capture/harness/atspi.py > "$ORACLE_OUT/atspi.log" 2>&1) & fi
  gnome-shell --headless $([ "${C_X11:-false}" = true ] || echo --no-x11) $(IFS=,; for m in ${C_MONITORS:-2560x1440}; do printf -- "--virtual-monitor %s " $m; done) --automation-script=/oracle/probe.js' >> "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
