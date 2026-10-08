#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Luma Calls against real PipeWire. Run as root in a disposable Fedora 44
# container from the repository root:
#   dnf5 -y install pipewire wireplumber pipewire-pulseaudio pipewire-utils \
#     pulseaudio-utils python3-gobject dbus-daemon dbus-x11 procps-ng chromium
#   bash tests/integration/luma-calls-pipewire.sh [native_app|chromium|broker_dbus]
set -euo pipefail
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export CALLS_TEST_RUN=/run/pwtest XDG_RUNTIME_DIR=/run/pwtest HOME=${HOME:-/root}
mkdir -p -m 700 "$XDG_RUNTIME_DIR"
pkill -f '^pipewire' || true; pkill -f '^wireplumber' || true; sleep 0.5
rm -rf "$HOME/.local/state/wireplumber"
eval "$(dbus-launch --sh-syntax)"
pipewire >"$XDG_RUNTIME_DIR/pipewire.log" 2>&1 &
sleep 1
wireplumber >"$XDG_RUNTIME_DIR/wireplumber.log" 2>&1 &
pipewire-pulse >"$XDG_RUNTIME_DIR/pulse.log" 2>&1 &
for _ in $(seq 50); do pactl info >/dev/null 2>&1 && break; sleep 0.1; done
pactl load-module module-null-sink sink_name=micfeed >/dev/null
pactl load-module module-null-sink sink_name=speakers >/dev/null
pactl load-module module-remap-source master=micfeed.monitor source_name=virtmic >/dev/null
pactl set-default-sink speakers
pactl set-default-source virtmic
python3 -c "
import math, struct
open('$XDG_RUNTIME_DIR/sine.raw','wb').write(b''.join(struct.pack('<h', int(12000*math.sin(2*math.pi*440*i/48000))) for i in range(96000)))"
# Stand-in applications: distinct executables, as a real app's streams carry.
for name in sinegen fakecall meter; do install -m 0755 /usr/bin/pacat "/usr/local/bin/$name"; done
bash -c "while true; do sinegen --playback -d micfeed --raw --format=s16le --rate=48000 --channels=1 < $XDG_RUNTIME_DIR/sine.raw; done" >/dev/null 2>&1 &
feeder=$!
trap 'kill $feeder 2>/dev/null; pkill -f "^pipewire" || true; pkill -f "^wireplumber" || true' EXIT
if [ "$#" -gt 0 ]; then
  python3 "$repo/tests/integration/luma-calls-pipewire.py" "$@"
else
  # The broker scenario publishes through the real broker, so it runs apart.
  status=0
  python3 "$repo/tests/integration/luma-calls-pipewire.py" native_app chromium || status=1
  python3 "$repo/tests/integration/luma-calls-pipewire.py" broker_dbus || status=1
  exit $status
fi
