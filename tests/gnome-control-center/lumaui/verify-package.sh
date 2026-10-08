#!/bin/sh
# Installed spec %check runtime assertions, after guarded host-build.
# Meson builds the exact existing scroll-speed C source; this never compiles.
set -eu
test "$#" -eq 1
build_dir=$1
test -x "$build_dir/panels/mouse/test-scroll-speed"
private_dir=$(mktemp -d "${TMPDIR:-/tmp}/luma-settings-package-check.XXXXXX")
trap 'rm -rf -- "$private_dir"' EXIT
mkdir -m 700 "$private_dir/runtime"
mkdir "$private_dir/config" "$private_dir/data" "$private_dir/cache" "$private_dir/state"
export XDG_RUNTIME_DIR="$private_dir/runtime"
export XDG_CONFIG_HOME="$private_dir/config" XDG_DATA_HOME="$private_dir/data"
export XDG_CACHE_HOME="$private_dir/cache" XDG_STATE_HOME="$private_dir/state"
export GSETTINGS_BACKEND=memory GDK_BACKEND=x11 GIO_USE_VFS=local
export DBUS_SYSTEM_BUS_ADDRESS="unix:path=$private_dir/no-system-bus"
unset DBUS_SESSION_BUS_ADDRESS DISPLAY WAYLAND_DISPLAY LUMA_SETTINGS_FIXTURE
meson test -C "$build_dir" --no-rebuild --num-processes 1 test-hostname --print-errorlogs
ui="$build_dir/panels/mouse/cc-mouse-panel.ui"
desktop="$build_dir/panels/mouse/gnome-mouse-panel.desktop"
grep -Fq '>TrackPoint<' "$ui"
grep -Fq 'translatable="yes">The small pointer control in the middle of your keyboard.<' "$ui"
if grep -Fq 'Pointing Stick' "$ui"; then exit 1; fi
grep -q '^Keywords=.*;TrackPoint;' "$desktop"
test "$(grep -c 'translatable="yes">Scroll _Speed<' "$ui")" -eq 2
"$build_dir/panels/mouse/test-scroll-speed" --tap > "$private_dir/scroll-speed.log"
cat "$private_dir/scroll-speed.log"
passed=$(grep -c '^ok ' "$private_dir/scroll-speed.log")
test "$passed" -eq 3
printf '%s\n' 'PASS: installed spec runtime assertions (compiler step performed by guarded Meson target)'
