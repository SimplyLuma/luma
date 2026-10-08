#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Run only in the owned disposable VM with GDM stopped and user1000 bus alive.
set -eu
ulimit -c 0
[ "${LUMA_NATIVE_SHELL_DISPOSABLE:-0}" = 1 ] || {
    printf '%s\n' 'Refusing: this fixture requires the disposable Shell VM.' >&2
    exit 2
}
[ "$(id -u)" = 0 ] && [ "$(id -u luma-media-test)" = 1000 ]
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
base=/var/tmp/luma-dash-native
width=${1:-1024}
variant=${2:-baseline}
case "$variant" in baseline|fixed|packaged) ;; *) exit 2 ;; esac
case "$width" in 1024|1366) ;; *) exit 2 ;; esac
mkdir -p "$base/overlay/ui" "$base/captures" "$base/config" "$base/data/applications"
cp "$root/tests/gnome-shell/dash-direct-arrangement.js" "$base/native-test.js"
mkdir -p "$base/folder"
if [ "$variant" = fixed ]; then
    cp "$root/tests/gnome-shell/dash-overlay/"*.js "$base/overlay/ui/"
else
    rm -f "$base/overlay/ui/shelf.js" "$base/overlay/ui/shelfArrange.js" "$base/overlay/ui/lumaDockFolders.js"
fi
favorites=$(python3 - "$root/config/desktop/dconf/db/luma.d/00-luma-desktop" <<'PY'
import ast, json, pathlib, sys
line = next(x for x in pathlib.Path(sys.argv[1]).read_text().splitlines()
            if x.startswith('favorite-apps='))
print(json.dumps(ast.literal_eval(line.split('=', 1)[1])))
PY
)
cat > "$base/overlay/ui/init.js" <<'JS'
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GIRepository from 'gi://GIRepository?version=3.0';
const repo = GIRepository.Repository.dup_default();
repo.prepend_library_path('/usr/lib64/gnome-shell');
repo.prepend_search_path('/usr/lib64/gnome-shell');
await import('./environment.js');
import {formatError} from '../misc/errorUtils.js';
imports._promiseNative.setMainLoopHook(() => {
    function failed(error) {
        logError(error, 'Packaged Shell follow-up fixture');
        global.context.terminate_with_error(new GLib.Error(
            Gio.IOErrorEnum, Gio.IOErrorEnum.FAILED, formatError(error)));
    }
    GLib.idle_add_once(GLib.PRIORITY_DEFAULT, () => {
        import('./main.js').then(main => {
            main.start();
            Gio.Resource.load('/usr/share/gnome-shell/gnome-shell-theme.gresource')._register();
            main.loadTheme();
        }).catch(failed);
    });
    GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 5000, () => {
        import('file:///var/tmp/luma-dash-native/native-test.js').then(async test => {
            await test.run();
            global.context.terminate();
        }).catch(failed);
    });
    global.context.run_main_loop();
});
JS
chown -R 1000:1000 "$base"
runuser -u luma-media-test -- env LUMA_FOLLOWUP_FAVORITES="$favorites" \
    XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    XDG_CURRENT_DESKTOP=GNOME GSETTINGS_BACKEND=keyfile \
    XDG_CONFIG_HOME="$base/config" XDG_DATA_HOME="$base/data" \
    XDG_DATA_DIRS=/usr/local/share:/usr/share \
    LD_LIBRARY_PATH=/usr/lib64/gnome-shell GI_TYPELIB_PATH=/usr/lib64/gnome-shell \
    G_RESOURCE_OVERLAYS="/org/gnome/shell=$base/overlay" \
    timeout 140 /usr/bin/gnome-shell --headless --wayland --no-x11 \
    --virtual-monitor="${width}x800" --wayland-display=wayland-dash \
    > "$base/native-$variant-$width.log" 2>&1
