#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
if test "$#" != 2; then
  echo 'Usage: run.sh PATCHED_SETTINGS_SOURCE SETTINGS_BUILD_DIRECTORY' >&2
  exit 2
fi
for tool in python3 gcc ninja pkg-config pulseaudio pactl Xvfb dbus-run-session timeout; do
  command -v "$tool" >/dev/null || { echo "Missing test dependency: $tool" >&2; exit 1; }
done
test "$(id -u)" != 0 || { echo 'Run this test as an ordinary user in a disposable build environment.' >&2; exit 1; }
test_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
runtime=$(mktemp -d /tmp/luma-sound-input-test-XXXXXX)
audio_pid= display_pid=
cleanup() {
  test -z "$audio_pid" || kill "$audio_pid" 2>/dev/null || true
  test -z "$display_pid" || kill "$display_pid" 2>/dev/null || true
  test -z "$audio_pid" || wait "$audio_pid" 2>/dev/null || true
  test -z "$display_pid" || wait "$display_pid" 2>/dev/null || true
  rm -rf "$runtime"
}
trap cleanup EXIT HUP INT TERM
python3 "$test_dir/compile.py" "$1" "$2" "$runtime/test-input-view"
export XDG_RUNTIME_DIR="$runtime" PULSE_SERVER="unix:$runtime/pulse.sock"
export GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory GDK_BACKEND=x11
# No default PulseAudio configuration, hardware modules or host audio socket.
pulseaudio -n --daemonize=no --use-pid-file=no --exit-idle-time=-1 \
  --log-target="file:$runtime/audio.log" \
  -L "module-native-protocol-unix socket=$runtime/pulse.sock auth-anonymous=1" \
  -L 'module-null-sink sink_name=test_output sink_properties=device.description=Test_output' \
  -L 'module-remap-source master=test_output.monitor source_name=mic_a source_properties=device.description=Test_microphone_A' \
  -L 'module-remap-source master=test_output.monitor source_name=mic_b source_properties=device.description=Test_microphone_B' &
audio_pid=$!
Xvfb -displayfd 3 -screen 0 1280x960x24 -nolisten tcp 3>"$runtime/display" >"$runtime/xvfb.log" 2>&1 &
display_pid=$!
tries=0
until test -s "$runtime/display" && pactl info >/dev/null 2>&1; do
  tries=$((tries+1))
  test "$tries" -lt 100 || { cat "$runtime/audio.log" "$runtime/xvfb.log" >&2; exit 1; }
  sleep 0.05
done
DISPLAY=":$(cat "$runtime/display")"; export DISPLAY
pactl set-default-source mic_a
pactl set-source-volume mic_b 10%
pactl set-source-mute mic_b 1
dbus-run-session -- timeout 30 "$runtime/test-input-view"
pactl get-source-volume mic_b
pactl get-source-mute mic_b
