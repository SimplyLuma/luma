#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
[ "${LUMA_NATIVE_SHELL_DISPOSABLE:-0}" = 1 ] && [ "$(id -u)" = 0 ] && [ "$(id -u luma-media-test)" = 1000 ]
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
name=${1:-candidate}
case "$name" in candidate|baseline|packaged) ;; *) exit 2 ;; esac
base=/var/tmp/luma-leaf-native-touch-$name
mkdir -p "$base/overlay/ui" "$base/control" "$base/config" "$base/data"
rm -f "$base/control/state.json" "$base/control/command"
cp "$root/tests/leaf/touch-native.js" "$base/touch-native.js"
# The overlay only starts the installed compositor and acceptance probe. It
# never replaces a product module, input handler, stylesheet or widget.
cat > "$base/overlay/ui/init.js" <<'JS'
import GLib from 'gi://GLib';import Gio from 'gi://Gio';import GIRepository from 'gi://GIRepository?version=3.0';const repo=GIRepository.Repository.dup_default();repo.prepend_library_path('/usr/lib64/gnome-shell');repo.prepend_search_path('/usr/lib64/gnome-shell');await import('./environment.js');import {formatError} from '../misc/errorUtils.js';
imports._promiseNative.setMainLoopHook(()=>{function failed(e){logError(e,'Native Leaf touch probe');global.context.terminate_with_error(new GLib.Error(Gio.IOErrorEnum,Gio.IOErrorEnum.FAILED,formatError(e)));}GLib.idle_add_once(GLib.PRIORITY_DEFAULT,()=>{import('./main.js').then(main=>{main.start();Gio.Resource.load('/usr/share/gnome-shell/gnome-shell-theme.gresource')._register();main.loadTheme();}).catch(failed);});GLib.timeout_add_once(GLib.PRIORITY_DEFAULT,5000,()=>{import(GLib.getenv('LUMA_LEAF_TOUCH_TEST')).then(async test=>{await test.run();global.context.terminate();}).catch(failed);});global.context.run_main_loop();});
JS
chown -R 1000:1000 "$base"
# For exact package acceptance, provide only private test fixtures, not product
# Python modules. Source preview is explicitly named candidate/baseline.
if [ "$name" = packaged ]; then
  mkdir -p "$base/fixtures/tests"
  cp "$root/src/luma-leaf/tests/__init__.py" "$root/src/luma-leaf/tests/fixtures.py" "$base/fixtures/tests/"
  python_path=$base/fixtures
else
  python_path=$root/src/luma-leaf
fi
ulimit -c 0
runuser -u luma-media-test -- env GDK_BACKEND=wayland WAYLAND_DISPLAY=wayland-leaf-touch-$name PYTHONPATH="$python_path" LUMA_LEAF_TOUCH_NOTE="${LUMA_LEAF_TOUCH_NOTE:-0}" LUMA_LEAF_TOUCH_WIDTH="${LUMA_LEAF_TOUCH_WIDTH:-1160}" LUMA_LEAF_TOUCH_FULLSCREEN="${LUMA_LEAF_TOUCH_FULLSCREEN:-0}" LUMA_LEAF_TOUCH_APP="$root/src/luma-leaf/tests/runtime_touch_native_app.py" LUMA_LEAF_TOUCH_TEST="file://$base/touch-native.js" LUMA_LEAF_TOUCH_CONTROL="$base/control" XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus XDG_CURRENT_DESKTOP=GNOME GSETTINGS_BACKEND=keyfile XDG_CONFIG_HOME="$base/config" XDG_DATA_HOME="$base/data" XDG_DATA_DIRS=/usr/local/share:/usr/share LD_LIBRARY_PATH=/usr/lib64/gnome-shell GI_TYPELIB_PATH=/usr/lib64/gnome-shell G_RESOURCE_OVERLAYS="/org/gnome/shell=$base/overlay" timeout 80 /usr/bin/gnome-shell --headless --wayland --no-x11 --virtual-monitor=1920x1080 --wayland-display=wayland-leaf-touch-$name > "$base/native.log" 2>&1
grep -q 'LEAF NATIVE COMPOSITOR TOUCH PASS' "$base/native.log"
grep -q 'LEAF NATIVE TOUCH APP PASS' "$base/native.log"
