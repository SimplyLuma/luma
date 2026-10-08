#!/bin/sh
# Private native chooser diagnosis; the real test assertions still run.
set -eu
test "$#" -eq 2
binary=$1
fixture=$2
test -x "$binary"
test -f "$fixture"
private_dir=$(mktemp -d "${TMPDIR:-/tmp}/luma-settings-folder-diagnosis.XXXXXX")
trap 'rm -rf -- "$private_dir"' EXIT
mkdir -m 700 "$private_dir/runtime"
mkdir "$private_dir/config" "$private_dir/data" "$private_dir/cache" "$private_dir/state"
export XDG_RUNTIME_DIR="$private_dir/runtime"
export XDG_CONFIG_HOME="$private_dir/config" XDG_DATA_HOME="$private_dir/data"
export XDG_CACHE_HOME="$private_dir/cache" XDG_STATE_HOME="$private_dir/state"
export GDK_BACKEND=x11 GDK_DEBUG=portals GSETTINGS_BACKEND=memory
export GTK_A11Y=none GSK_RENDERER=cairo G_DEBUG=fatal-criticals GIO_USE_VFS=local
export DBUS_SYSTEM_BUS_ADDRESS="unix:path=$private_dir/no-system-bus"
export LUMA_SETTINGS_TEST_FIXTURE="$fixture"
unset WAYLAND_DISPLAY LUMA_SETTINGS_FIXTURE LUMA_SETTINGS_APPLICATION_ID
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dbus-run-session --config-file="$script_dir/private-session.conf" -- xvfb-run -a \
  "$binary" -p /settings/folders/private-portal-route --GTestSubprocess
