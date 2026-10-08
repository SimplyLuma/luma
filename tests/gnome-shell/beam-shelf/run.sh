#!/bin/bash
# Beam oracle run (inside luma-shell-oracle). B_TAG B_THEME(light|dark|frost|glass) B_EDGE B_SCALE B_OVERLAY B_EVAL B_MONITORS B_ANIM(true|false) B_EXTRA_KEYS
set -u
T=beam-${B_TAG:-a}
export XDG_RUNTIME_DIR=/tmp/$T-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/$T-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/fonts $HOME/.local/share/applications
cp /oracle/dock-parity/fonts/*.ttf $HOME/.local/share/fonts/
theme=${B_THEME:-light}; scheme=default; [ "$theme" = dark ] || [ "$theme" = glass ] && scheme=prefer-dark
[ "$theme" = glass ] && scheme=default
cat > $HOME/.config/glib-2.0/settings/keyfile <<KEY
[org/project-luma/shell-state]
reduce-transparency=false
shelf-edge='${B_EDGE:-bottom}'
shelf-edge-mode='floating'
shelf-float-ends=true
shelf-padding=8
shelf-span-full=${B_SPAN:-false}
shelf-surface-mode='${B_SURFACE:-separate}'
status-controls-right=true
surface-treatment='$theme'

[org/gnome/desktop/interface]
color-scheme='$scheme'
enable-animations=${B_ANIM:-true}
toolkit-accessibility=${B_A11Y:-false}

[org/gnome/shell]
favorite-apps=['org.oracle.Claude.desktop', 'org.oracle.Third.desktop']
welcome-dialog-last-shown-version='999'
enabled-extensions=['appindicatorsupport@rgcjonas.gmail.com']
KEY
printf '[Desktop Entry]\nType=Application\nName=Claude\nExec=python3 /oracle/live-island/harness/win.py org.oracle.Claude Claude\nIcon=org.gnome.Settings\n' > $HOME/.local/share/applications/org.oracle.Claude.desktop
printf '[Desktop Entry]\nType=Application\nName=Third\nExec=python3 /oracle/live-island/harness/win.py org.oracle.Third Third\nIcon=org.gnome.Settings\n' > $HOME/.local/share/applications/org.oracle.Third.desktop
printf '[Desktop Entry]\nType=Application\nName=Second\nExec=python3 /oracle/live-island/harness/win.py org.oracle.Second Second\nIcon=org.gnome.Nautilus\n' > $HOME/.local/share/applications/org.oracle.Second.desktop
[ -d /oracle/live-island/schemas89 ] && export GSETTINGS_SCHEMA_DIR=/oracle/live-island/schemas89
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1
export ORACLE_OUT=/oracle/beam/out-${B_TAG:-a} ORACLE_EVAL=${B_EVAL:-/oracle/beam/harness/eval.js} ORACLE_SETTLE=${ORACLE_SETTLE:-9000}
[ "${B_OVERLAY:-}" != none ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=${B_OVERLAY:-/oracle/beam/overlay}/js/ui:/org/gnome/shell/theme=${B_OVERLAY:-/oracle/beam/overlay}/data/theme"
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1; then rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork 2>/dev/null; fi
rm -rf /run/systemd/seats
timeout ${B_TIMEOUT:-170} dbus-run-session -- bash -c '
  (sleep 4; gapplication launch org.oracle.Claude > "$ORACLE_OUT/win.log" 2>&1 || python3 /oracle/live-island/harness/win.py org.oracle.Claude Claude >> "$ORACLE_OUT/win.log" 2>&1) &
  (sleep 5; python3 /oracle/live-island/harness/win.py org.oracle.Second Second >> "$ORACLE_OUT/win2.log" 2>&1) &
  if [ -n "${B_PIPEWIRE:-}" ]; then
    pipewire > "$ORACLE_OUT/pipewire.log" 2>&1 & sleep 1; wireplumber > "$ORACLE_OUT/wireplumber.log" 2>&1 &
    sleep 1; pipewire-pulse > "$ORACLE_OUT/pipewire-pulse.log" 2>&1 &
    for i in $(seq 40); do pactl info >/dev/null 2>&1 && break; sleep 0.25; done
    pactl load-module module-null-sink sink_name=speakers sink_properties=device.description=Speakers >/dev/null 2>&1
    pactl set-default-sink speakers >/dev/null 2>&1
  fi
  if [ "${B_A11Y:-false}" = true ]; then (sleep 7; python3 /oracle/beam/harness/atspi.py > "$ORACLE_OUT/atspi.log" 2>&1) & fi
  if [ -n "${B_LIVE:-}" ]; then LIVE_CASE=$B_LIVE python3 /oracle/live-island/harness/fake.py > "$ORACLE_OUT/fake.log" 2>&1 & fi
  if [ -n "${B_SNI:-}" ]; then (sleep 5; ORACLE_SNI_COUNT=$B_SNI python3 /oracle/sni.py > "$ORACLE_OUT/sni.log" 2>&1) & fi
  gnome-shell --headless --no-x11 $(IFS=,; for m in ${B_MONITORS:-2560x1440}; do printf -- "--virtual-monitor %s " $m; done) --automation-script=/oracle/probe.js' > "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
