#!/bin/sh
# Native fixture runtime smoke: private display/bus, no real-data preview.
set -eu
if [ "$#" -ne 4 ]; then
  echo "Usage: smoke-app.sh <native-binary> <settings-v70.json> <page> <desktop|phone>" >&2
  exit 2
fi
binary=$1
fixture=$2
page=$3
case "$4" in
  desktop) phone=0;;
  phone) phone=1;;
  *) echo "Invalid smoke presentation" >&2; exit 2;;
esac
test -x "$binary"
test -f "$fixture"
private_dir=$(mktemp -d "${TMPDIR:-/tmp}/luma-settings-smoke.XXXXXX")
trap 'rm -rf -- "$private_dir"' EXIT
mkdir "$private_dir/config" "$private_dir/data" "$private_dir/cache" "$private_dir/state"
mkdir -m 700 "$private_dir/runtime"
export XDG_RUNTIME_DIR="$private_dir/runtime"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dbus-run-session --config-file="$script_dir/private-session.conf" -- xvfb-run -a -s '-screen 0 1280x1024x24' env \
  -u WAYLAND_DISPLAY -u LUMA_SETTINGS_APPLICATION_ID \
  -u LUMA_SETTINGS_VARIANT -u LUMA_SETTINGS_QUERY -u APP_ID -u APPLICATION_ID \
  GDK_BACKEND=x11 GSK_RENDERER=cairo GTK_A11Y=none GSETTINGS_BACKEND=memory \
  XDG_CONFIG_HOME="$private_dir/config" XDG_DATA_HOME="$private_dir/data" \
  XDG_CACHE_HOME="$private_dir/cache" XDG_STATE_HOME="$private_dir/state" \
  G_DEBUG=fatal-criticals DBUS_SYSTEM_BUS_ADDRESS="unix:path=$private_dir/no-system-bus" \
  LUMA_SETTINGS_FIXTURE="$fixture" LUMA_SETTINGS_PAGE="$page" \
  LUMA_SETTINGS_SMOKE=1 LUMA_SETTINGS_SMOKE_PHONE="$phone" \
  "$binary"
