#!/bin/sh
# Final-runtime isolation; call this INSIDE toolbox, never before toolbox.
set -eu
test "$#" -gt 0
private_dir=$(mktemp -d "${TMPDIR:-/tmp}/luma-settings-native-private.XXXXXX")
trap 'rm -rf -- "$private_dir"' EXIT
mkdir -m 700 "$private_dir/runtime"
mkdir "$private_dir/config" "$private_dir/data" "$private_dir/cache" "$private_dir/state"
export XDG_RUNTIME_DIR="$private_dir/runtime"
export XDG_CONFIG_HOME="$private_dir/config" XDG_DATA_HOME="$private_dir/data"
export XDG_CACHE_HOME="$private_dir/cache" XDG_STATE_HOME="$private_dir/state"
export GSETTINGS_BACKEND=memory GDK_BACKEND=x11 GSK_RENDERER=cairo GTK_A11Y=none GIO_USE_VFS=local
export DBUS_SYSTEM_BUS_ADDRESS="unix:path=$private_dir/no-system-bus"
unset WAYLAND_DISPLAY LUMA_SETTINGS_FIXTURE LUMA_SETTINGS_APPLICATION_ID
unset LUMA_SETTINGS_SMOKE LUMA_SETTINGS_SMOKE_PHONE APP_ID APPLICATION_ID
unset LUMA_SETTINGS_PAGE LUMA_SETTINGS_QUERY LUMA_SETTINGS_VARIANT
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dbus-run-session --config-file="$script_dir/private-session.conf" -- xvfb-run -a -s '-screen 0 1280x1024x24' "$@"
