#!/bin/bash
# Only a private session bus, runtime directory and headless display are used.
set -euo pipefail
ari_worktree=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
ari_scratch=$(mktemp -d /tmp/ari-lumaui-runtime.XXXXXX)
trap 'rm -rf "$ari_scratch"' EXIT
mkdir -m 700 "$ari_scratch/runtime" "$ari_scratch/config" "$ari_scratch/data" "$ari_scratch/cache" "$ari_scratch/state"
mkdir -m 700 "$ari_scratch/data/photos"
# Staged modules have no repository-relative fallback. Keep approved fixture
# portraits in the private data directory, as the conform capture does.
cp "$ari_worktree/tests/fixtures/ari-v70-data/photos/"*.webp "$ari_scratch/data/photos/"
export XDG_RUNTIME_DIR="$ari_scratch/runtime" XDG_CONFIG_HOME="$ari_scratch/config"
export XDG_DATA_HOME="$ari_scratch/data" XDG_STATE_HOME="$ari_scratch/state"
export XDG_CACHE_HOME="$ari_scratch/cache"
export GSETTINGS_BACKEND=memory GDK_BACKEND=wayland GSK_RENDERER=cairo
export PYTHONPATH="$ari_worktree/src/luma-platform/appkit:${ARI_LUMAUI_STAGE_PATH:-$ari_worktree/src/luma-ari}"
export LUMA_APPKIT_ICON_PATH="$ari_worktree/assets/icon-theme/Prairie/symbolic/actions"
export LUMA_ARI_FIXTURE="$ari_worktree/tests/fixtures/ari-v70.json"
export ARI_LUMAUI_RUNTIME_SCRIPT="$ari_worktree/src/luma-ari/tests/lumaui_runtime.py"
ari_socket=luma-ari-runtime-$$
export DBUS_SESSION_BUS_ADDRESS="unix:path=$ari_scratch/bus"
printf '%s' '<busconfig><type>session</type><auth>EXTERNAL</auth><policy context="default"><allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>' > "$ari_scratch/bus.conf"
ari_cleanup() {
  ari_test_status=$?
  if [ "$ari_test_status" -ne 0 ]; then
    tail -n 20 -- "$ari_scratch/mutter.log" "$ari_scratch/broker.log" >&2
  fi
  pkill -u "$USER" -f "^/usr/bin/mutter --headless --wayland --no-x11 --wayland-display=$ari_socket --virtual-monitor 1920x1200$" || true
  pkill -u "$USER" -f "^/usr/bin/dbus-broker-launch --scope=user --config-file=$ari_scratch/bus.conf$" || true
  pkill -u "$USER" -f "^/usr/bin/systemd-socket-activate .* $ari_scratch/bus .*" || true
  rm -rf "$ari_scratch"
}
trap ari_cleanup EXIT
/usr/bin/systemd-socket-activate -E DBUS_SESSION_BUS_ADDRESS -E XDG_RUNTIME_DIR -l "$ari_scratch/bus" /usr/bin/dbus-broker-launch --scope=user "--config-file=$ari_scratch/bus.conf" > "$ari_scratch/broker.log" 2>&1 &
# Leave room around the 1180px desktop window so Mutter does not auto-maximize
# it and persist that maximized state into the following phone checks.
/usr/bin/mutter --headless --wayland --no-x11 "--wayland-display=$ari_socket" --virtual-monitor 1920x1200 > "$ari_scratch/mutter.log" 2>&1 &
for ari_try in $(seq 1 100); do
  [ -S "$XDG_RUNTIME_DIR/$ari_socket" ] && break
  sleep .1
done
[ -S "$XDG_RUNTIME_DIR/$ari_socket" ] || { tail -n 20 -- "$ari_scratch/mutter.log"; exit 1; }
export WAYLAND_DISPLAY="$ari_socket" GTK_A11Y=none
unset DISPLAY
if [ "${ARI_LUMAUI_KIT_TESTS:-0}" = 1 ]; then
  cd "$ari_worktree"
  python3 -m unittest discover -s tests/unit -p 'test_luma_appkit*.py'
else
  python3 "$ARI_LUMAUI_RUNTIME_SCRIPT" -v
fi
