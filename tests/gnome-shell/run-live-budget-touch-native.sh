#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
[ "${LUMA_NATIVE_SHELL_DISPOSABLE:-0}" = 1 ] && [ "$(id -u)" = 0 ] && [ "$(id -u luma-media-test)" = 1000 ]
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
width=${1:-1366};case "$width" in 1366|1440) ;; *) exit 2 ;; esac
b=/var/tmp/luma-live-budget-touch-native
mkdir -p "$b/overlay/ui" "$b/config" "$b/data/applications" "$b/captures"
# Native package acceptance must never accidentally retain a preview module.
[ ! -e "$b/overlay/ui/shelf.js" ] && [ ! -e "$b/overlay/ui/lumaNotificationBeacon.js" ]
cp "$root/tests/gnome-shell/live-budget-touch-native.js" "$b/native-test.js"
cp "$root/tests/gnome-shell/live-budget-mpris.py" "$b/provider.py"
favorites=$(python3 - "$root/config/desktop/dconf/db/luma.d/00-luma-desktop" <<'PY2'
import ast,json,pathlib,sys
line=next(x for x in pathlib.Path(sys.argv[1]).read_text().splitlines() if x.startswith('favorite-apps='))
print(json.dumps(ast.literal_eval(line.split('=',1)[1])))
PY2
)
cat > "$b/overlay/ui/init.js" <<'JS'
import GLib from 'gi://GLib';import Gio from 'gi://Gio';import GIRepository from 'gi://GIRepository?version=3.0';const repo=GIRepository.Repository.dup_default();repo.prepend_library_path('/usr/lib64/gnome-shell');repo.prepend_search_path('/usr/lib64/gnome-shell');await import('./environment.js');import {formatError} from '../misc/errorUtils.js';
imports._promiseNative.setMainLoopHook(()=>{function failed(e){logError(e,'Native activity/touch probe');global.context.terminate_with_error(new GLib.Error(Gio.IOErrorEnum,Gio.IOErrorEnum.FAILED,formatError(e)));}GLib.idle_add_once(GLib.PRIORITY_DEFAULT,()=>{import('./main.js').then(main=>{main.start();Gio.Resource.load('/usr/share/gnome-shell/gnome-shell-theme.gresource')._register();main.loadTheme();}).catch(failed);});GLib.timeout_add_once(GLib.PRIORITY_DEFAULT,5000,()=>{import('file:///var/tmp/luma-live-budget-touch-native/native-test.js').then(async test=>{await test.run();global.context.terminate();}).catch(failed);});global.context.run_main_loop();});
JS
chown -R 1000:1000 "$b"
ulimit -c 0
runuser -u luma-media-test -- env LUMA_ACTIVITY_FAVORITES="$favorites" LUMA_ACTIVITY_PROVIDER="$b/provider.py" LUMA_MEDIA_ACTION_LOG="$b/media-actions.log" LUMA_ACTIVITY_EVIDENCE="$b/captures" XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus XDG_CURRENT_DESKTOP=GNOME GSETTINGS_BACKEND=keyfile XDG_CONFIG_HOME="$b/config" XDG_DATA_HOME="$b/data" XDG_DATA_DIRS=/usr/local/share:/usr/share LD_LIBRARY_PATH=/usr/lib64/gnome-shell GI_TYPELIB_PATH=/usr/lib64/gnome-shell G_RESOURCE_OVERLAYS="/org/gnome/shell=$b/overlay" timeout 70 /usr/bin/gnome-shell --headless --wayland --no-x11 --virtual-monitor="${width}x800" --wayland-display=wayland-live-touch-native > "$b/native-$width.log" 2>&1
grep -q 'NATIVE LIVE BUDGET AND TOUCH PASS' "$b/native-$width.log"
