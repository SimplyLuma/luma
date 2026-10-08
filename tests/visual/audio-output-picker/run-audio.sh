#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
# Headless GNOME Shell render of the Sound Output detail and the AirPlay
# picker (Shell series patch 0119-luma-audio-output-picker.patch), for the
# Luma shell render oracle container: a Fedora 44 container with the exact
# gnome-shell/mutter RPMs, pipewire, wireplumber, pipewire-pulseaudio and
# pipewire-utils, whose /oracle holds probe.js and the keyfiles. It keeps its
# own runtime, home and output directories so it can run beside other renders.
#
# AUDIO_DIR: this directory plus luma-network-speaker-symbolic.svg and
#   org.projectluma.AudioDevices1.xml copied in
# ORACLE_OVERLAY: a directory with js/ui/status/volume.js and data/theme/*.css
#   from the patched tree
# ORACLE_OUT: output directory; ORACLE_THEME dark|light or ORACLE_KEYFILE
# AUDIO_NO_SERVICE=1: render without org.projectluma.AudioDevices
set -u
tag=audio-policy-$$
export XDG_RUNTIME_DIR=/tmp/$tag-run; rm -rf $XDG_RUNTIME_DIR; mkdir -m 700 -p $XDG_RUNTIME_DIR
export HOME=/tmp/$tag-home; rm -rf $HOME; mkdir -p $HOME/.config/glib-2.0/settings $HOME/.local/share/luma
cp ${ORACLE_KEYFILE:-/oracle/keyfile.${ORACLE_THEME:-dark}} $HOME/.config/glib-2.0/settings/keyfile 2>/dev/null || cp /oracle/keyfile $HOME/.config/glib-2.0/settings/keyfile
mkdir -p $HOME/.local/share/icons/hicolor/scalable/status
cp "$AUDIO_DIR/luma-network-speaker-symbolic.svg" $HOME/.local/share/icons/hicolor/scalable/status/
export AUDIO_NO_SERVICE=${AUDIO_NO_SERVICE:-} GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 ORACLE_OUT=${ORACLE_OUT:?}
export G_RESOURCE_OVERLAYS="/org/gnome/shell/ui=$ORACLE_OVERLAY/js/ui:/org/gnome/shell/theme=$ORACLE_OVERLAY/data/theme"
export ORACLE_EVAL=$AUDIO_DIR/airplay-render.js ORACLE_SETTLE=${ORACLE_SETTLE:-9000}
rm -rf "$ORACLE_OUT"; mkdir -p "$ORACLE_OUT"
mkdir -p /run/dbus; [ -S /run/dbus/system_bus_socket ] || dbus-daemon --system --fork 2>/dev/null; rm -rf /run/systemd/seats
timeout 150 dbus-run-session -- bash -c '
  pipewire > "$ORACLE_OUT/pipewire.log" 2>&1 &
  sleep 1
  wireplumber > "$ORACLE_OUT/wireplumber.log" 2>&1 &
  pipewire-pulse > "$ORACLE_OUT/pulse.log" 2>&1 &
  sleep 2
  mk() { pw-cli create-node adapter "{ factory.name=support.null-audio-sink media.class=Audio/Sink object.linger=true audio.position=[FL FR] $1 }" >/dev/null; }
  mk "node.name=alsa_output.pci-0000_00_1f.3.HiFi__Speaker__sink node.description=Speaker device.api=alsa priority.session=1000"
  mk "node.name=alsa_output.pci-0000_00_1f.3.HiFi__HDMI1__sink node.description=\"LG UltraWide\" device.api=alsa priority.session=600"
  mk "node.name=luma_airplay.aa0000000001 node.description=\"Living Room\" node.network=true luma.airplay.id=aa0000000001 priority.session=10"
  mk "node.name=raop_sink.Neighbour-Mac.local.192.0.2.9.7000 node.description=\"Neighbour’s MacBook Pro\" node.network=true priority.session=10"
  sleep 1
  pw-metadata 0 default.configured.audio.sink "{ \"name\": \"alsa_output.pci-0000_00_1f.3.HiFi__Speaker__sink\" }" Spa:String:JSON >/dev/null
  [ -n "${AUDIO_NO_SERVICE:-}" ] || python3 "$AUDIO_DIR/fake_audio_devices.py" "$AUDIO_DIR/org.projectluma.AudioDevices1.xml" > "$ORACLE_OUT/fake.log" 2>&1 &
  sleep 1
  gnome-shell --headless --no-x11 --virtual-monitor ${ORACLE_W:-1920}x${ORACLE_H:-1200} --automation-script=/oracle/probe.js' > "$ORACLE_OUT/shell.log" 2>&1
echo "exit $?"
