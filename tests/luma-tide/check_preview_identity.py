# SPDX-License-Identifier: Apache-2.0
"""Run on a private session bus/display; never start Tide stores or a window.

dbus-run-session -- xvfb-run -a env LUMA_TIDE_IDENTITY_PRIVATE_BUS=1 \
    PYTHONPATH=src/luma-platform/appkit:src/luma-tide \
    python3 tests/luma-tide/check_preview_identity.py
"""
import gc
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

private_address = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
if (not __debug__ or os.environ.get('LUMA_TIDE_IDENTITY_PRIVATE_BUS') != '1'
        or not private_address.startswith(('unix:path=/tmp/dbus-', 'unix:abstract=/tmp/dbus-'))):
    raise SystemExit('Run this check through the documented private-bus command')
sandbox = tempfile.TemporaryDirectory(prefix='tide-identity-')
for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
    os.environ[key] = sandbox.name + '/' + key
os.environ['GSETTINGS_BACKEND'] = 'memory'
os.environ.pop('LUMA_TIDE_FIXTURE', None)

import gi
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib
from luma_tide import preview
from luma_tide import application as tide_application
from luma_tide.application import TideApplication
from luma_tide.identity import APP_ID, PREVIEW_APP_ID, mpris_name
from luma_tide.mpris import MprisService
from luma_tide.playback import PlaybackSnapshot


def drain_until(predicate):
    deadline = time.monotonic() + 3
    context = GLib.MainContext.default()
    while not predicate():
        while context.pending():
            context.iteration(False)
        if time.monotonic() > deadline:
            raise AssertionError('private-bus identity did not settle')
        time.sleep(.01)


bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
assert tide_application.APP_ID == APP_ID
assert TideApplication().get_application_id() == APP_ID


def bus_call(method, signature, values, result_type):
    return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                         'org.freedesktop.DBus', method, GLib.Variant(signature, values),
                         GLib.VariantType.new(result_type), Gio.DBusCallFlags.NONE,
                         1000, None).unpack()[0]


def has_owner(name):
    return bus_call('NameHasOwner', '(s)', (name,), '(b)')


def remote_activate(application_id, native=False):
    # GApplication clients need separate process connections: registering two
    # local owners on Gio's shared connection collides at the exported path.
    source = 'from luma_tide.application import TideApplication; app = TideApplication(application_id=' + repr(application_id) + ')' if native else 'from gi.repository import Gio; app = Gio.Application(application_id=' + repr(application_id) + ')'
    child = subprocess.Popen([sys.executable, '-c', source + '; assert app.register(None); assert app.get_is_remote(); app.activate()'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    drain_until(lambda: child.poll() is not None)
    stdout, stderr = child.communicate(timeout=1)
    assert child.returncode == 0, (stdout, stderr)


production = Gio.Application(application_id=APP_ID)
activations = {'production': 0, 'preview': 0}
startup_calls = []
production.connect('activate', lambda *_: activations.__setitem__('production', activations['production'] + 1))
assert production.register(None) and not production.get_is_remote()
production_owner = bus_call('GetNameOwner', '(s)', (APP_ID,), '(s)')
production_mpris = Gio.bus_own_name_on_connection(bus, mpris_name(APP_ID),
                                                Gio.BusNameOwnerFlags.NONE, None, None)
drain_until(lambda: has_owner(mpris_name(APP_ID)))


def verify_run(application, _argv):
    # This is the actual Gtk/GApplication created through preview.main().
    assert application.get_application_id() == PREVIEW_APP_ID
    assert application.get_flags() == Gio.ApplicationFlags.HANDLES_OPEN
    assert application.register(None) and not application.get_is_remote()
    assert startup_calls == [PREVIEW_APP_ID]
    assert application.window is None
    assert application.get_windows() == []
    assert has_owner(PREVIEW_APP_ID)
    assert application.get_dbus_object_path() == '/org/projectluma/Tide/LumaUIPreview'
    assert bus_call('GetNameOwner', '(s)', (APP_ID,), '(s)') == production_owner
    assert activations == {'production': 0, 'preview': 1}
    remote_activate(APP_ID)
    drain_until(lambda: activations['production'] == 1)
    assert activations == {'production': 1, 'preview': 1}
    remote_activate(PREVIEW_APP_ID, native=True)
    drain_until(lambda: activations['preview'] == 2)
    assert activations['production'] == 1
    controller = SimpleNamespace(snapshot=PlaybackSnapshot(), subscribe=lambda _callback: lambda: None)
    service = MprisService(controller, application_id=application.get_application_id(),
                           raise_window=lambda: None, quit_application=lambda: None)
    try:
        drain_until(lambda: has_owner(mpris_name(PREVIEW_APP_ID)))
        assert service._root_property('DesktopEntry').unpack() == PREVIEW_APP_ID
        assert has_owner(mpris_name(APP_ID))
    finally:
        service.close()
    drain_until(lambda: not has_owner(mpris_name(PREVIEW_APP_ID)))
    assert has_owner(mpris_name(APP_ID)) and application.window is None
    assert application.get_windows() == []
    application.quit()
    return 0


# Register explicit probe vfuncs at class creation. Changing a Python attribute
# afterward need not replace a native GType's installed virtual function.
# The inherited Tide constructor and native unique registration stay unchanged.
class IdentityProbe(TideApplication):
    def do_startup(self):
        startup_calls.append(self.get_application_id())
        Adw.Application.do_startup(self)
        self.hold()

    def do_activate(self):
        activations['preview'] += 1

    def do_shutdown(self):
        Adw.Application.do_shutdown(self)

    def run(self, argv):
        failures = []

        def verify():
            try:
                verify_run(self, argv)
            except BaseException as error:
                failures.append(error)
            finally:
                self.quit()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(verify)
        result = Gio.Application.run(self, argv or ['tide-preview-identity'])
        if failures:
            raise failures[0]
        return result


with patch('luma_tide.application.open_library_store', side_effect=AssertionError('identity check must not open a library')) as store_open, \
     patch.object(tide_application, 'TideApplication', IdentityProbe):
    assert preview.main([]) == 0
    store_open.assert_not_called()
assert tide_application.APP_ID == APP_ID
assert TideApplication().get_application_id() == APP_ID
gc.collect()
drain_until(lambda: not has_owner(PREVIEW_APP_ID))
assert bus_call('GetNameOwner', '(s)', (APP_ID,), '(s)') == production_owner
Gio.bus_unown_name(production_mpris)
sandbox.cleanup()
print(f'PASS: native preview={PREVIEW_APP_ID}; production={APP_ID}; activations={activations}; preview application/MPRIS names released; no windows or real data opened')
