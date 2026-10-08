#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Headless integration run for luma-audio-policy inside a Fedora 44 container.
#
# Needs: pipewire pipewire-utils pipewire-pulseaudio pipewire-config-raop
# wireplumber wireplumber-libs python3-gobject dbus-daemon glib2 dconf lua, plus
# luma-audio-policy installed (the RPM, or `meson install` of
# src/luma-audio-devices into /usr). For the AirPlay scenario also avahi,
# nss-mdns and a reachable AirPlay receiver (see scenario_airplay.py).
#
# Usage: run-headless.sh [outputs|airplay|all]
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
what=${1:-all}
run_dir=$(mktemp -d /tmp/luma-audio-it.XXXXXX)
export XDG_RUNTIME_DIR=$run_dir/runtime HOME=$run_dir/home
export XDG_STATE_HOME=$HOME/.local/state XDG_CONFIG_HOME=$HOME/.config
export GSETTINGS_BACKEND=dconf FAKE_DESKTOP_LOG=$run_dir/desktop.jsonl
export LUMA_AUDIO_DEVICES_LOGFILE=$run_dir/service.log
mkdir -p "$XDG_RUNTIME_DIR" "$HOME" && chmod 700 "$XDG_RUNTIME_DIR"
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null; done
  wait 2>/dev/null
}
trap cleanup EXIT
status=0

# In a container without an init, every PipeWire client's RTKit request makes
# the system bus activate rtkit-daemon, which exits and stays a zombie until the
# container runs out of PIDs. Real-time scheduling is irrelevant here.
if [ -f /run/.containerenv ] && [ -f /usr/share/dbus-1/system-services/org.freedesktop.RealtimeKit1.service ]; then
  mv /usr/share/dbus-1/system-services/org.freedesktop.RealtimeKit1.service /usr/share/dbus-1/system-services/org.freedesktop.RealtimeKit1.service.disabled-for-tests
fi
if command -v avahi-daemon >/dev/null; then
  if ! pgrep -f '[d]bus-daemon --system' >/dev/null; then
    mkdir -p /run/dbus && rm -f /run/dbus/system_bus_socket /run/dbus/pid /run/dbus/messagebus.pid
    dbus-daemon --system --fork
    sleep 1
  fi
  if ! avahi-daemon --check 2>/dev/null; then
    rm -f /run/avahi-daemon/pid
    avahi-daemon --daemonize --no-drop-root 2>/dev/null || true
  fi
fi

dbus-daemon --session --address="unix:path=$XDG_RUNTIME_DIR/bus" --fork --nopidfile --print-pid >"$run_dir/bus.pid"
pids+=("$(cat "$run_dir/bus.pid")")
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
python3 "$here/fake_desktop.py" >"$run_dir/fake-desktop.out" 2>&1 & pids+=($!)
pipewire >"$run_dir/pipewire.log" 2>&1 & pids+=($!)
sleep 1
WIREPLUMBER_DEBUG=s-luma-output-policy:4 wireplumber >"$run_dir/wireplumber.log" 2>&1 & pids+=($!)
pipewire-pulse >"$run_dir/pulse.log" 2>&1 & pids+=($!)
sleep 2

echo "== system policy"
if pw-cli ls Module | grep -q 'libpipewire-module-raop-discover'; then
  echo "FAIL module-raop-discover is loaded despite module.raop = false"; status=1
else
  echo "PASS module-raop-discover is not loaded (Fedora's 50-raop.conf is overridden)"
fi
if [ -f /usr/lib64/pipewire-0.3/libpipewire-module-raop-sink.so ]; then
  echo "PASS libpipewire-module-raop-sink stays installed"
else
  echo "FAIL libpipewire-module-raop-sink is missing"; status=1
fi
# A person can still opt back in, per account.
opt_in=$run_dir/opt-in
mkdir -p "$opt_in/pipewire/pipewire.conf.d"
printf 'context.properties = { module.raop = true }\n' >"$opt_in/pipewire/pipewire.conf.d/60-raop-discover.conf"
PIPEWIRE_CORE=luma-opt-in-check XDG_CONFIG_HOME=$opt_in pipewire >"$run_dir/opt-in.log" 2>&1 & opt_pid=$!
sleep 1.5
if PIPEWIRE_REMOTE=luma-opt-in-check pw-cli ls Module | grep -q 'libpipewire-module-raop-discover'; then
  echo "PASS a per-account module.raop = true fragment turns discovery back on"
else
  echo "FAIL the per-account opt-in fragment did not load module-raop-discover"; status=1
fi
kill "$opt_pid"; wait "$opt_pid" 2>/dev/null
if grep -q "luma/output-policy.lua" "$run_dir/wireplumber.log" && grep -qiE "error|failed to load.*luma" <(grep -i luma "$run_dir/wireplumber.log"); then
  echo "FAIL WirePlumber reported a problem loading the Luma policy"; status=1
fi
if wpctl settings luma.audio.manual-only-outputs >/dev/null 2>&1; then
  echo "PASS WirePlumber still declares the retired luma.audio.manual-only-outputs, so old values can be removed"
else
  echo "FAIL luma.audio.manual-only-outputs is not declared; saved values from 1.luma.1-2 cannot be removed"; status=1
fi

if [ "$what" = outputs ] || [ "$what" = all ]; then
  echo "== output scenarios"
  python3 "$here/scenario_outputs.py" || status=1
fi
if [ "$what" = airplay ] || [ "$what" = all ]; then
  echo "== AirPlay scenarios"
  python3 "$here/scenario_airplay.py" || status=1
fi

echo "logs: $run_dir"
grep -h "s-luma-output-policy" "$run_dir/wireplumber.log" | tail -n 40 >"$run_dir/policy-decisions.txt" || true
exit $status
