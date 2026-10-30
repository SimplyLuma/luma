#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Run against installed candidate resources in an owned disposable fixture.
set -euo pipefail
[ "${LUMA_NATIVE_SHELL_DISPOSABLE:-0}" = 1 ]
[ "$(id -u)" = 0 ] && [ "$(id -u luma-media-test)" = 1000 ]
polish_repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
polish_out=${DEV_ARTIFACT_DIR:?Supply the private fixture artifact directory}
mkdir -p "$polish_out"/{config/glib-2.0/settings,data/applications,runtime}
chmod 700 "$polish_out/runtime"
cat > "$polish_out/config/glib-2.0/settings/keyfile" <<'SETTINGS'
[org/gnome/shell]
enabled-extensions=[]
disable-user-extensions=true
welcome-dialog-last-shown-version='999'
favorite-apps=[]
[org/gnome/mutter]
experimental-features=['scale-monitor-framebuffer']
[org/gnome/desktop/interface]
clock-format='12h'
enable-animations=false
SETTINGS
case ${LUMA_POLISH_SCENARIO:-status-polish} in
 status-polish) polish_min_checks=89 ;;
 applications-drawer|drawer-app-actions|dock-scroll)
  polish_min_checks=28
  if [ "${LUMA_POLISH_SCENARIO}" = dock-scroll ]; then polish_min_checks=8; fi
  mkdir -p "$polish_out/Downloads"
  printf 'A real fixture download\n' > "$polish_out/Downloads/notes.txt"
  printf 'XDG_DOWNLOAD_DIR="%s/Downloads"\n' "$polish_out" > "$polish_out/config/user-dirs.dirs"
  cat > "$polish_out/data/applications/org.projectluma.DrawerProbe.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Luma Drawer Probe
Exec=python3 $polish_repo/tests/gnome-shell/shelf-presentation/app.py org.projectluma.DrawerProbe
Icon=utilities-terminal
Terminal=false
EOF
  ;;
 *) echo 'Unknown native polish scenario' >&2; exit 2 ;;
esac
cp "$polish_repo/tests/gnome-shell/${LUMA_POLISH_SCENARIO:-status-polish}/native.js" "$polish_out/native.js"
chown -R 1000:1000 "$polish_out"
# Podman can create an empty seats directory without supplying logind.
# Refuse a populated seat tree: this fixture has no physical seat to use.
if [ -d /run/systemd/seats ]; then rmdir /run/systemd/seats; fi
# The disposable container must supply a private /run. Keep the standard
# address so activated fixture services connect to this same bus.
[ ! -S /run/dbus/system_bus_socket ]
mkdir -p /run/dbus
polish_bus_pid=$(dbus-daemon --system --nopidfile --fork --print-pid)
trap 'kill "$polish_bus_pid" 2>/dev/null || true' EXIT
ulimit -c 0
runuser -u luma-media-test -- env \
 XDG_RUNTIME_DIR="$polish_out/runtime" XDG_CONFIG_HOME="$polish_out/config" \
 XDG_DATA_HOME="$polish_out/data" GSETTINGS_BACKEND=keyfile \
 DBUS_SYSTEM_BUS_ADDRESS="unix:path=/run/dbus/system_bus_socket" \
 LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1 \
 LUMA_POLISH_OUTPUT="$polish_out" dbus-run-session -- \
 timeout 120 gnome-shell --headless --no-x11 --virtual-monitor=1440x1000 \
 --automation-script="$polish_out/native.js" > "$polish_out/shell.log" 2>&1
python3 - "$polish_out/result.json" "$polish_min_checks" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
for check in result['results']:
    if not check['ok']:
        print(check)
assert result['pass'] and len(result['results']) >= int(sys.argv[2])
print('Native status polish: PASS', len(result['results']), 'checks')
PY
