#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Run only in an owned disposable native Shell container.
set -euo pipefail
[[ ${LUMA_NATIVE_SHELL_DISPOSABLE:-0} == 1 && $(id -u) == 0 ]]
[[ $(id -u luma-media-test) == 1000 ]]
repo=$(cd "$(dirname "$0")/../../.." && pwd)
out=${DEV_ARTIFACT_DIR:?}/five-monitors
case ${LUMA_FIVE_SCALE:-1} in 1|1.25) ;; *) exit 2 ;; esac
case ${LUMA_FIVE_LAYOUT:-stacked} in
 stacked) side=1280x720; centre=2560x1440 ;;
 wide) side=1920x1080; centre=5120x1440 ;;
 *) exit 2 ;;
esac
mkdir -p "$out"/{config/glib-2.0/settings,data,runtime}
chmod 700 "$out/runtime"
cat > "$out/config/glib-2.0/settings/keyfile" <<'EOF'
[org/gnome/shell]
enabled-extensions=['tilingshell@ferrarodomenico.com']
disable-user-extensions=false
welcome-dialog-last-shown-version='999'
favorite-apps=[]
[org/gnome/mutter]
experimental-features=['scale-monitor-framebuffer']
[org/project-luma/shell-state]
surface-treatment='dark'
EOF
cp "$repo/tests/tiling-shell/five-monitors/native.js" "$out/native.js"
if [[ -d /run/systemd/seats ]]; then rmdir /run/systemd/seats; fi
[[ ! -S /run/dbus/system_bus_socket ]]
mkdir -p /run/dbus
bus_pid=$(dbus-daemon --system --nopidfile --fork --print-pid)
trap 'kill "$bus_pid" 2>/dev/null || true' EXIT
chown -R 1000:1000 "$out"
ulimit -c 0
runuser -u luma-media-test -- env XDG_RUNTIME_DIR="$out/runtime" XDG_CONFIG_HOME="$out/config" \
 XDG_DATA_HOME="$out/data" GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1 \
 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1 LUMA_FIVE_OUT="$out" LUMA_FIVE_SCALE="${LUMA_FIVE_SCALE:-1}" \
 LUMA_FIVE_APP="$repo/tests/gnome-shell/shelf-presentation/app.py" dbus-run-session -- \
 timeout 180 gnome-shell --headless --no-x11 --virtual-monitor="$side" --virtual-monitor="$side" \
 --virtual-monitor="$side" --virtual-monitor="$side" --virtual-monitor="$centre" \
 --automation-script="$out/native.js" > "$out/shell.log" 2>&1
python3 - "$out/result.json" <<'PY'
import json,sys
result=json.load(open(sys.argv[1]))
for item in result['results']:
 if not item['ok']:print(item)
assert result['pass'] and len(result['results'])>=28
print('Native five-monitor dragging: PASS',len(result['results']),'checks')
PY
