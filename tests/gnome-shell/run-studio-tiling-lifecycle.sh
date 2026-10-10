#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Run only as root in a disposable container with matching Shell/Mutter RPMs.
set -eu
[ "${LUMA_NATIVE_SHELL_DISPOSABLE:-0}" = 1 ] && [ "$(id -u)" = 0 ] && [ "$(id -u luma-media-test)" = 1000 ]
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d /var/tmp/luma-studio-tiling-XXXXXX)
mkdir -p "$work/overlay/ui" "$work/overlay/misc" "$work/config" "$work/data" "$work/runtime"
chmod 700 "$work/runtime"
cp "$root/studio-tiling-lifecycle.js" "$work/probe.js"
# Simulate GDM eligibility only inside this owned headless fixture. Use the
# installed module so the fixture cannot silently test a stale upstream copy.
# Real authentication remains outside this test; no hardware session is touched.
gresource extract /usr/lib64/gnome-shell/libshell-18.so \
 /org/gnome/shell/misc/loginManager.js > "$work/loginManager-installed.js"
python3 - "$work/loginManager-installed.js" "$work/overlay/misc/loginManager.js" <<'PY_FIXTURE'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
start = source.index('function haveSystemd() {')
end = source.index('\n}', start) + 2
source = source[:start] + 'function haveSystemd() { return false; }' + source[end:]
start = source.index('export function canLock() {')
end = source.index('\n}', start) + 2
source = source[:start] + 'export function canLock() { return true; }' + source[end:]
Path(sys.argv[2]).write_text(source)
PY_FIXTURE
# Optional production-module candidate overlay, keeping all other installed
# Shell modules intact. Omit this argument for a packaged native acceptance run.
if [ "$#" -gt 0 ]; then
 for module in prairieLogin unlockDialog quickSettings; do
  if [ -f "$1/$module.js" ]; then
   cp "$1/$module.js" "$work/overlay/ui/$module.js"
  fi
 done
fi
cat > "$work/overlay/ui/init.js" <<'JS'
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GIRepository from 'gi://GIRepository?version=3.0';
const repository = GIRepository.Repository.dup_default();
repository.prepend_library_path('/usr/lib64/gnome-shell');
repository.prepend_search_path('/usr/lib64/gnome-shell');
await import('./environment.js');
imports._promiseNative.setMainLoopHook(() => {
    const failed = error => {
        logError(error, 'Studio tiling native lifecycle test');
        global.context.terminate_with_error(new GLib.Error(Gio.IOErrorEnum, Gio.IOErrorEnum.FAILED, String(error)));
    };
    GLib.idle_add_once(GLib.PRIORITY_DEFAULT, () => {
        import('./main.js').then(main => {
            new Gio.Settings({schema_id: 'org.gnome.shell'}).set_string('welcome-dialog-last-shown-version', '999');
            main.start();
            Gio.Resource.load('/usr/share/gnome-shell/gnome-shell-theme.gresource')._register();
            main.loadTheme();
        }).catch(failed);
    });
    GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 5000, () => {
        import(`file://${GLib.getenv('LUMA_STUDIO_TILING_PROBE')}`).then(async test => {
            await test.run();
            global.context.terminate();
        }).catch(failed);
    });
    global.context.run_main_loop();
});
JS
mkdir -p /run/dbus
if ! dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
 rm -f /run/dbus/pid /run/dbus/system_bus_socket
 dbus-daemon --system --fork
fi
chown -R 1000:1000 "$work"
ulimit -c 0
printf 'Native studio tiling evidence: %s\n' "$work"
rpm -q gnome-shell mutter
runuser -u luma-media-test -- env G_DEBUG= XDG_RUNTIME_DIR="$work/runtime" \
 XDG_CONFIG_HOME="$work/config" XDG_DATA_HOME="$work/data" \
 XDG_CURRENT_DESKTOP=GNOME GSETTINGS_BACKEND=memory \
 LIBGL_ALWAYS_SOFTWARE=1 LUMA_MUTTER_ALLOW_SOFTWARE_BLUR=1 GTK_A11Y=none \
 LD_LIBRARY_PATH=/usr/lib64/gnome-shell GI_TYPELIB_PATH=/usr/lib64/gnome-shell \
 LUMA_STUDIO_TILING_PROBE="$work/probe.js" \
 G_RESOURCE_OVERLAYS="/org/gnome/shell=$work/overlay" \
 dbus-run-session -- timeout 75 /usr/bin/gnome-shell --headless --wayland --no-x11 \
 --virtual-monitor=1366x800 --wayland-display=wayland-studio-tiling-lifecycle > "$work/native.log" 2>&1
grep 'NATIVE PACKAGED TILING PASS' "$work/native.log"
