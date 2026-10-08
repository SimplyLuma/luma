#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Install, launch, update and remove real apps through Depot's own provider.

Acceptance harness for a disposable Luma VM, run as an ordinary user in a
graphical-less session (``dbus-run-session``), with the installed
luma-application-installer. It drives luma_depot.native exactly as the window
does -- the same provider, the same system helper through pkexec, the same
catalogue -- and prints one JSON line per step:

    python3 depot_channel_acceptance.py install gimp nordvpn mega ...
    python3 depot_channel_acceptance.py remove gimp ...
    python3 depot_channel_acceptance.py update nordvpn ...
    python3 depot_channel_acceptance.py state gimp ...

pkexec needs an authorization: on the test VM a polkit rule for the test user
allows org.projectluma.application-installer.system (never on a real system).
Nothing here signs in to any app.
"""

import json
import os
import sys
import threading
import time

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib  # noqa: E402

from luma_depot import native  # noqa: E402

loop = GLib.MainLoop()


def run(call, *args, timeout=3600):
    """Call a provider method with a callback; wait for the Result."""
    box = {}
    progress = []

    def on_progress(value):
        progress.append((round(value.fraction, 2), value.stage))
        return False

    def done(result):
        box['result'] = result
        loop.quit()
    if call.__name__ in ('install', 'update'):
        call(*args, on_progress, done)
    elif call.__name__ == 'remove':
        call(*args, keep_data=True, callback=done)
    else:
        call(*args, done)
    GLib.timeout_add_seconds(timeout, loop.quit)
    loop.run()
    return box.get('result'), progress


def snapshot():
    return native.NativeCatalogue().snapshot()


def report(**fields):
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def app_for(catalogue, identifier):
    app = catalogue.find('catalog:' + identifier)
    if app is None:
        raise SystemExit(f'{identifier} is not in the catalogue')
    return app


def state(identifier):
    catalogue = snapshot()
    app = app_for(catalogue, identifier)
    result, _ = run(native.NativeInstallation().installed)
    installed = {record.app_id: record for record in (result.value or ())}
    record = installed.get(app.app_id)
    report(step='state', app=identifier, installable=app.installable, availability=app.availability,
           source=app.source_label, installed=record is not None,
           version=record.version if record else '', managed=bool(record and record.managed))
    return app, record


def main(argv):
    action, identifiers = argv[0], argv[1:]
    installer = native.NativeInstallation()
    for identifier in identifiers:
        started = time.monotonic()
        app, record = state(identifier)
        if action == 'state':
            continue
        if action == 'install':
            result, progress = run(installer.install, app)
        elif action == 'update':
            result, progress = run(installer.update, app.app_id)
        elif action == 'remove':
            result, progress = run(installer.remove, app.app_id)
        else:
            raise SystemExit(f'unknown action {action}')
        error = result.error if result is not None else None
        report(step=action, app=identifier, ok=bool(result and result.ok),
               seconds=round(time.monotonic() - started, 1),
               stages=sorted({stage for _fraction, stage in progress}),
               last_fraction=progress[-1][0] if progress else None,
               error=(error.hint or str(error)) if error else '',
               detail=getattr(error, 'detail', '')[-400:] if error else '')
        after_app, after = state(identifier)
        if action == 'install' and after is not None:
            # "Open": the launcher Depot would start must exist and be valid.
            try:
                record = installer._record(after.app_id)
                info = record.app_info
                report(step='launcher', app=identifier, desktop_id=info.get_id(),
                       executable=info.get_executable(), found=True)
            except Exception as problem:  # noqa: BLE001 - reported, not raised
                state_ = native.channel_apps.State(__import__(
                    'luma_installer.depot_catalog', fromlist=['local_catalog']).local_catalog())
                files = state_.desktop_files.get(identifier, ())
                report(step='launcher', app=identifier, found=bool(files), desktop_files=list(files),
                       note=str(problem))


if __name__ == '__main__':
    main(sys.argv[1:])
