#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Test fixture only; no product overlay, package change, or hardware write.
set -eu
[ "${LUMA_NATIVE_SHELL_DISPOSABLE:-0}" = 1 ] || {
    printf '%s\n' 'Refusing: use only the owned disposable native Shell VM.' >&2
    exit 2
}
[ "$(id -u)" = 0 ] && [ "$(id -u luma-media-test)" = 1000 ]
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
width=${1:-1366}
case "$width" in 1366|1440) ;; *) exit 2 ;; esac
base=/var/tmp/luma-shell104-native
mkdir -p "$base/overlay/ui" "$base/captures" "$base/config" "$base/data/applications"
cp "$root/tests/gnome-shell/status-lock-native.js" "$base/native-test.js"
cat > "$base/overlay/ui/init.js" <<'JS'
import GLib from 'gi://GLib';import Gio from 'gi://Gio';import GIRepository from 'gi://GIRepository?version=3.0';
const repo=GIRepository.Repository.dup_default();repo.prepend_library_path('/usr/lib64/gnome-shell');repo.prepend_search_path('/usr/lib64/gnome-shell');await import('./environment.js');import {formatError} from '../misc/errorUtils.js';
imports._promiseNative.setMainLoopHook(()=>{function failed(e){logError(e,'Native clock probe');global.context.terminate_with_error(new GLib.Error(Gio.IOErrorEnum,Gio.IOErrorEnum.FAILED,formatError(e)));}GLib.idle_add_once(GLib.PRIORITY_DEFAULT,()=>{import('./main.js').then(main=>{main.start();Gio.Resource.load('/usr/share/gnome-shell/gnome-shell-theme.gresource')._register();main.loadTheme();}).catch(failed);});GLib.timeout_add_once(GLib.PRIORITY_DEFAULT,5000,()=>{import('file:///var/tmp/luma-shell104-native/native-test.js').then(async test=>{await test.run();global.context.terminate();}).catch(failed);});global.context.run_main_loop();});
JS
chown -R 1000:1000 "$base"
ulimit -c 0
runuser -u luma-media-test -- env XDG_RUNTIME_DIR=/run/user/1000 \
 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
 XDG_CURRENT_DESKTOP=GNOME GSETTINGS_BACKEND=keyfile \
 XDG_CONFIG_HOME="$base/config" XDG_DATA_HOME="$base/data" \
 XDG_DATA_DIRS=/usr/local/share:/usr/share \
 LD_LIBRARY_PATH=/usr/lib64/gnome-shell GI_TYPELIB_PATH=/usr/lib64/gnome-shell \
 G_RESOURCE_OVERLAYS="/org/gnome/shell=$base/overlay" \
 LUMA_STATUS_LOCK_CAPTURE="$base/captures" timeout 130 \
 /usr/bin/gnome-shell --headless --wayland --no-x11 \
 --virtual-monitor="${width}x800" --virtual-monitor="800x600" \
 --wayland-display=wayland-shell104-native \
 > "$base/native-$width.log" 2>&1
grep -q 'Native status/lock checks: PASS' "$base/native-$width.log"
if grep -q '^FAIL ' "$base/native-$width.log"; then exit 1; fi
printf '%s\n' "Native installed Shell status/lock/OSD fixture: PASS ($width)"
