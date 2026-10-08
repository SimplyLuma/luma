#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# What a Flatpak app can do with com.canonical.Unity.LauncherEntry through its
# D-Bus proxy, with the filter Flatpak gives an ordinary app (no --talk-name
# for com.canonical.Unity). Emitting the broadcast must work; owning the
# service name and calling it must not. Run inside a session bus:
#   dbus-run-session -- bash flatpak-proxy.sh
set -u
P=${XDG_RUNTIME_DIR:-/tmp}/launcher-proxy
rm -f "$P"
# The filter an app gets with no extra D-Bus permissions: its own name, plus
# the portals. Nothing about com.canonical.Unity.
xdg-dbus-proxy "$DBUS_SESSION_BUS_ADDRESS" "$P" \
  --filter --own=org.example.Badger --talk=org.freedesktop.portal.Desktop &
proxy=$!
for i in $(seq 50); do [ -S "$P" ] && break; sleep 0.1; done
export SANDBOXED_BUS=unix:path=$P
result=0
say() { printf '%s %s\n' "$1" "$2"; [ "$1" = PASS ] || result=1; }

# 1. A watcher on the real bus, and the app emitting Update through the proxy.
gdbus monitor --session --dest :1.0 >/dev/null 2>&1 &
python3 - <<'PY'
import os, sys, time
from gi.repository import Gio, GLib
real = Gio.bus_get_sync(Gio.BusType.SESSION, None)
seen = []
real.signal_subscribe(None, 'com.canonical.Unity.LauncherEntry', 'Update', None, None,
                      Gio.DBusSignalFlags.NONE,
                      lambda *a: seen.append(a[5].unpack()))
sandboxed = Gio.DBusConnection.new_for_address_sync(
    os.environ['SANDBOXED_BUS'],
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None, None)
sandboxed.emit_signal(None, '/com/example/badger', 'com.canonical.Unity.LauncherEntry', 'Update',
                      GLib.Variant('(sa{sv})', ('application://org.example.Badger.desktop',
                                                {'count': GLib.Variant('x', 4),
                                                 'count-visible': GLib.Variant('b', True)})))
sandboxed.flush_sync(None)
deadline = time.monotonic() + 5
while not seen and time.monotonic() < deadline:
    GLib.MainContext.default().iteration(False)
    time.sleep(0.02)
print('EMIT', 'reached the session bus' if seen else 'was dropped', seen)
# Owning com.canonical.Unity from the sandbox must be refused by the filter.
try:
    reply = sandboxed.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                'RequestName', GLib.Variant('(su)', ('com.canonical.Unity', 4)),
                                GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
    print('OWN', 'allowed', reply.unpack())
except Exception as error:
    print('OWN', 'refused', type(error).__name__)
sys.exit(0 if seen else 1)
PY
emitted=$?
kill $proxy 2>/dev/null
[ $emitted -eq 0 ] && say PASS "a sandboxed app's LauncherEntry Update crosses its proxy" \
                   || say FAIL "the proxy dropped the LauncherEntry Update"
exit $result
