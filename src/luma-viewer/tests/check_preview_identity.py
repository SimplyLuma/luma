# SPDX-License-Identifier: Apache-2.0
"""Verify the real scratch launcher on a private bus, without activation.

Run on the GTK test host after process-capacity recovery:
    python3 src/luma-viewer/tests/check_preview_identity.py
No Viewer window is created and no document/history is opened.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

PRODUCTION = 'org.projectluma.Viewer'
PREVIEW = PRODUCTION + '.LumaUIPreview'


def worker():
    from luma_viewer.application import ViewerApplication
    app = ViewerApplication()
    expected = PREVIEW if os.environ.get('LUMA_VIEWER_PREVIEW') else PRODUCTION
    assert app.get_application_id() == expected, app.get_application_id()
    if expected == PREVIEW:
        assert os.environ.get('LUMA_VIEWER_PREVIEW_STATE_ROOT'), 'Preview history is not isolated'
        assert app.register(None)
        assert not app.get_is_remote(), 'Preview registered as a remote application'
    assert not app.get_windows(), 'Identity check created a window'
    print(json.dumps(dict(application_id=app.get_application_id(),
                         preview_environment=os.environ.get('LUMA_VIEWER_PREVIEW'),
                         native_type=app.__gtype__.name,
                         application_module=sys.modules[ViewerApplication.__module__].__file__,
                         windows=len(app.get_windows()),
                         remote=app.get_is_remote() if expected == PREVIEW else None)))
    app.quit()


def private_bus(installed_launcher=None):
    assert os.environ['DBUS_SESSION_BUS_ADDRESS'] != os.environ.get('VIEWER_IDENTITY_ORIGINAL_BUS'), 'Private bus required'
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    production = Gio.Application(application_id=PRODUCTION)
    activations = []
    production.connect('activate', lambda *_: activations.append(True))
    assert production.register(None) and not production.get_is_remote()
    bus = production.get_dbus_connection()

    def call(method, name, signature):
        return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                             'org.freedesktop.DBus', method,
                             GLib.Variant('(s)', (name,)), GLib.VariantType(signature),
                             Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]

    owner = call('GetNameOwner', PRODUCTION, '(s)')
    source = Path(__file__).resolve().parents[1]
    repo = source.parents[1]
    with tempfile.TemporaryDirectory(prefix='viewer-identity-') as temporary:
        root = Path(temporary)
        (root / 'bin').mkdir(); (root / 'python').mkdir()
        (root / 'share/luma-viewer').mkdir(parents=True)
        shutil.copy2(source / 'viewer.css', root / 'share/luma-viewer/viewer.css')
        (root / 'python/luma_viewer').symlink_to(source / 'luma_viewer', target_is_directory=True)
        (root / 'python/luma_appkit').symlink_to(repo / 'src/luma-platform/appkit/luma_appkit', target_is_directory=True)
        launcher = root / 'bin/luma-viewer-preview'
        shutil.copy2(source / 'bin/luma-viewer-preview', launcher)
        launcher.chmod(0o755)
        wrapper = root / 'bin/python3'
        wrapper.write_text('#!/bin/sh\nset -eu\n[ "$1" = "-m" ]\n[ "$2" = "luma_viewer.application" ]\nexec ' +
                           shlex.quote(sys.executable) + ' ' + shlex.quote(str(Path(__file__).resolve())) + ' --worker\n')
        wrapper.chmod(0o755)
        env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                   PYTHONPATH=str(root / 'python'))
        for key in ('LUMA_VIEWER_PREVIEW', 'LUMA_VIEWER_PREVIEW_STATE_ROOT',
                    'LUMA_VIEWER_FIXTURE', 'LUMA_VIEWER_STATE',
                    'LUMA_VIEWER_SELECTED', 'LUMA_VIEWER_MODE'):
            env.pop(key, None)
        default = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                 env=env, check=True, text=True, capture_output=True)
        preview_launcher = Path(installed_launcher).resolve() if installed_launcher else launcher
        assert preview_launcher.read_text().strip().splitlines()[-1] == 'exec python3 -m luma_viewer.application "$@"', 'Review launcher interception before running this probe'
        preview = subprocess.run([str(preview_launcher)], env=env, check=True, text=True, capture_output=True)
        evidence = [json.loads(output.stdout.splitlines()[-1]) for output in (default, preview)]
        assert evidence[0]['application_id'] == PRODUCTION
        assert evidence[1]['application_id'] == PREVIEW
        assert evidence[1]['preview_environment'] == '1'
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        assert not activations, 'Production instance was activated'
        assert call('GetNameOwner', PRODUCTION, '(s)') == owner
        deadline = time.monotonic() + 5
        while call('NameHasOwner', PREVIEW, '(b)') and time.monotonic() < deadline:
            time.sleep(.05)
        assert not call('NameHasOwner', PREVIEW, '(b)'), 'Preview bus owner remains after exit'
        print(json.dumps(dict(pass_identity=True, applications=evidence,
                             production_activations=len(activations), production_owner_preserved=True,
                             preview_closed=True, private_bus=True)))
    production.quit()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--private-bus', action='store_true')
    parser.add_argument('--launcher', help='Verify the installed scratch launcher instead of a temporary copy')
    options = parser.parse_args()
    if options.worker:
        worker()
    elif options.private_bus:
        private_bus(options.launcher)
    else:
        env = dict(os.environ, VIEWER_IDENTITY_ORIGINAL_BUS=os.environ.get('DBUS_SESSION_BUS_ADDRESS', ''))
        command = ['dbus-run-session', '--', sys.executable,
                   str(Path(__file__).resolve()), '--private-bus']
        if options.launcher:
            command += ['--launcher', options.launcher]
        raise SystemExit(subprocess.run(command, env=env).returncode)
