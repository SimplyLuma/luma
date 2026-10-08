# SPDX-License-Identifier: Apache-2.0
"""Disposable-VM integration. Run with Monitor closed; --quit-editor requests
normal closure of an explicitly opened GNOME Text Editor test document only.
"""
import json
import os
import sys
from gi.repository import Gio, GLib

NAME = 'org.gnome.Shell.Introspect'
PATH = '/org/gnome/Shell/Introspect'

def call(bus, method, args=None, signature=None):
    return bus.call_sync(NAME, PATH, NAME, method, args,
                         GLib.VariantType.new(signature) if signature else None,
                         Gio.DBusCallFlags.NO_AUTO_START, 3000, None).unpack()[0]

def denied(bus, method, args=None):
    try:
        call(bus, method, args)
    except GLib.Error as error:
        assert error.matches(Gio.dbus_error_quark(), Gio.DBusError.ACCESS_DENIED), str(error)
    else:
        raise AssertionError(f'{method} exposed to unauthorized caller')

# A separate authenticated session connection owns no allowlisted name.
stranger = Gio.DBusConnection.new_for_address_sync(
    os.environ['DBUS_SESSION_BUS_ADDRESS'],
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None, None)
denied(stranger, 'GetMonitorApplications')
denied(stranger, 'RequestMonitorQuit', GLib.Variant('(sau)', ('org.gnome.TextEditor.desktop', [])))
stranger.close_sync(None)
app = Gio.Application(application_id='io.luma.Monitor', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
app.register(None)
assert not app.get_is_remote(), 'Close Monitor before running this fixture'
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
apps = call(bus, 'GetMonitorApplications', signature='(a{sau})')
assert isinstance(apps, dict)
assert apps, 'No running applications returned'
denied(bus, 'GetWindows')
denied(bus, 'GetRunningApplications')
assert not call(bus, 'RequestMonitorQuit', GLib.Variant('(sau)', ('missing.desktop', [1])), '(b)')
print('PASS real D-Bus stranger denied; Monitor allowed only narrow API; missing selection refused')
print(json.dumps(apps, sort_keys=True))
from luma_monitor.integration import DesktopCatalog
from luma_monitor.model import Sampler
catalog = DesktopCatalog()
catalog.refresh_running()
snapshot = Sampler(resolve=catalog.resolve).sample()
print('Resolved app rows:', [r['name'] for r in snapshot['rows'] if not r['background']])
for app_id in apps:
    if app_id.removesuffix('.desktop') in (catalog.entries | getattr(catalog, "hidden_entries", {})):
        assert any(r['id'] == 'app:' + app_id for r in snapshot['rows']), app_id
catalog.close()
if '--quit-editor' in sys.argv:
    target = 'org.gnome.TextEditor.desktop'
    assert apps.get(target), 'Open only the disposable Editor document first'
    assert not call(bus, 'RequestMonitorQuit', GLib.Variant('(sau)', (target, [])), '(b)')
    assert call(bus, 'RequestMonitorQuit', GLib.Variant('(sau)', (target, apps[target])), '(b)')
    print('PASS normal Editor quit requested; inspect actual unsaved prompt before claiming preservation')
