#!/bin/bash
# Sound oracle run inside luma-beam-sound. S_TAG S_THEME(freedesktop|luma) S_OVERLAY(dir|"") S_PLAYING(0|1) S_GSD(path)
set -u
T=beamsnd-${S_TAG}
export XDG_RUNTIME_DIR=/tmp/$T-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/$T-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings
export OUT=/beam/sound/out-${S_TAG}; rm -rf $OUT; mkdir -p $OUT
cat > $HOME/.config/glib-2.0/settings/keyfile <<KEY
[org/gnome/desktop/sound]
event-sounds=true
${S_THEME:+theme-name='$S_THEME'}

[org/project-luma/shell-state]
surface-treatment='dark'

[org/gnome/desktop/interface]
color-scheme='prefer-dark'

[org/gnome/shell]
welcome-dialog-last-shown-version='999'
KEY
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 NO_AT_BRIDGE=1
[ -n "${S_OVERLAY:-}" ] && export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$S_OVERLAY"
rm -rf /run/systemd/seats; mkdir -p /run/dbus; dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.GetId >/dev/null 2>&1 || { rm -f /run/dbus/system_bus_socket /run/dbus/pid; dbus-daemon --system --fork; }
timeout ${S_TIMEOUT:-150} dbus-run-session -- bash -c '
  pipewire > $OUT/pipewire.log 2>&1 &
  sleep 1; wireplumber > $OUT/wireplumber.log 2>&1 &
  sleep 1; pipewire-pulse > $OUT/pipewire-pulse.log 2>&1 &
  for i in $(seq 40); do pactl info >/dev/null 2>&1 && break; sleep 0.25; done
  pactl load-module module-null-sink sink_name=events sink_properties=device.description=events > /dev/null
  pactl load-module module-null-sink sink_name=music sink_properties=device.description=music > /dev/null
  pactl set-default-sink events
  sleep 1
  pw-mon 2>&1 | python3 -u -c "import sys,time
for l in sys.stdin: sys.stdout.write(f\"{time.time():.3f} {l}\")" > $OUT/pw-mon.log &
  date +%s.%N > $OUT/record-start
  parec -d events.monitor --rate=48000 --channels=1 --format=s16le > $OUT/events.raw &
  if [ "${S_PLAYING:-0}" = 1 ]; then
    # Something playing on the default output: the case GNOME skips its pop for.
    (sleep 3; pactl set-default-sink events; pacat --raw --format=s16le --rate=48000 --channels=1 -d events /beam/sound/tone.raw > $OUT/tone.log 2>&1) &
  fi
  (sleep 9; ${S_GSD:-/usr/libexec/gsd-media-keys} > $OUT/gsd-media-keys.log 2>&1) &
  gnome-shell --headless --no-x11 --virtual-monitor 1920x1080 --automation-script=/beam/sound/probe.js > $OUT/shell.log 2>&1
  pkill -INT -f "^parec" ; sleep 1
' > $OUT/session.log 2>&1
echo "exit $?"
