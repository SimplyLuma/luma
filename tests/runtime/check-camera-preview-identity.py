#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the preview launcher's identity hook on a private bus/display.

Pass the installed scratch preview's bin/run, or the sync script before
installation. No CameraWindow, device, encoder or photo store is opened.
"""
import gc
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

runtime = Path(os.environ['XDG_RUNTIME_DIR'])
assert str(runtime).startswith('/tmp/lumaui-camera-test.'), 'requires the private Camera test runner'
bus_address = os.environ['DBUS_SESSION_BUS_ADDRESS']
assert ';' not in bus_address and bus_address.split(',')[0] == f'unix:path={runtime}/bus'
assert os.environ['XDG_DATA_HOME'] == str(runtime / 'data')
for variable, folder in (('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                         ('XDG_STATE_HOME', 'state')):
    assert os.environ[variable] == str(runtime / folder)
assert os.environ['GSETTINGS_BACKEND'] == 'memory'
assert (os.environ.get('WAYLAND_DISPLAY') == 'lumaui-camera-fixture-test'
        or (os.environ.get('GDK_BACKEND') == 'x11'
            and os.environ.get('DISPLAY', '').startswith(':')))
launcher = Path(sys.argv[1]).resolve(strict=True)
if launcher.parent.name == 'bin':
    preview_python = launcher.parent.parent / 'python'
    assert (preview_python / 'prairie_apps/camera.py').is_file()
    sys.path.insert(0, str(preview_python))
Gtk.init()
Adw.init()
from prairie_apps import camera

if launcher.parent.name == 'bin':
    assert Path(camera.__file__).resolve().is_relative_to(preview_python)
commands = [shlex.split(line) for line in launcher.read_text().splitlines()
            if line.startswith('exec python3 -c ')]
assert len(commands) == 1, 'expected one Prairie preview launcher hook'
code = commands[0][3].replace('$app', 'camera')
assert 'm.APP_ID' in code and 'm.main()' in code
production_id = camera.APPLICATION_ID
assert production_id == camera.APP_ID == 'org.projectluma.Camera'
preview_id = production_id + '.LumaUIPreview'
activations = []
production = Gio.Application(application_id=production_id)
production.connect('activate', lambda *_: activations.append('production'))
assert production.register(None) and not production.get_is_remote()
bus = production.get_dbus_connection()


def owns(name):
    return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                         'org.freedesktop.DBus', 'NameHasOwner',
                         GLib.Variant('(s)', (name,)), GLib.VariantType.new('(b)'),
                         Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]


probed = []


def identity_probe():
    assert camera.APP_ID == preview_id
    assert camera.APPLICATION_ID == production_id
    app = camera.CameraApplication()
    assert app.get_application_id() == preview_id
    app.hold()
    errors = []
    window = SimpleNamespace(present=Mock())

    def verify():
        try:
            assert app.get_is_registered() and not app.get_is_remote()
            assert owns(preview_id) and owns(production_id)
            window.present.assert_called_once()
            assert not activations, 'preview activated the production identity'
            assert not app.get_windows(), 'identity probe opened a window'
            probed.append(preview_id)
        except BaseException as error:
            errors.append(error)
        finally:
            app.quit()
        return GLib.SOURCE_REMOVE

    GLib.idle_add(verify)
    with patch.object(camera, 'CameraWindow', return_value=window), \
         patch.object(camera, 'list_camera_devices', side_effect=AssertionError('device access')), \
         patch.object(camera, 'PhotoLibrary', side_effect=AssertionError('store access')), \
         patch.object(camera, 'VideoRecording', side_effect=AssertionError('encoder access')):
        result = app.run([])
    app.run_dispose()
    del app
    gc.collect()
    assert not errors, errors
    assert result == 0 and probed == [preview_id]
    assert not owns(preview_id), 'preview still owns its bus identity after closing'
    assert owns(production_id) and not activations
    return result


with patch.dict(os.environ, {}, clear=False), patch.object(camera, 'main', identity_probe):
    os.environ.pop('LUMA_CAMERA_FIXTURE', None)
    try:
        exec(compile(code, str(launcher), 'exec'), {})
    except SystemExit as result:
        assert result.code == 0
camera.APP_ID = production_id
assert camera.APPLICATION_ID == production_id
with patch.dict(os.environ):
    os.environ.pop('LUMA_CAMERA_FIXTURE', None)
    assert camera.CameraApplication().get_application_id() == production_id
assert probed == [preview_id] and not activations
print(f'Camera preview identity: PASS launcher={launcher} production={production_id} '
      f'preview={preview_id} module={camera.__file__} '
      'production-activations=0 preview-closed=True', flush=True)
