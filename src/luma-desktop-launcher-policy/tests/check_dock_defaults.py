#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read compiled dock defaults and persistent personal precedence via dconf.

Use the actual installed Shell schema and isolated account/profile on a private
bus. This does not launch a compositor or change the builder's account settings.
"""
import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SCHEMA = 'org.gnome.shell'
KEY = 'favorite-apps'
EXPECTED = [
    'org.gnome.Nautilus.desktop', 'viola-browser.desktop',
    'org.projectluma.Ari.desktop',
    'org.projectluma.Notes.desktop', 'org.projectluma.Photos.desktop',
    'org.projectluma.Tide.desktop', 'org.projectluma.Calendar.desktop',
    'org.projectluma.Tasks.desktop', 'org.projectluma.Contacts.desktop',
    'org.projectluma.Messages.desktop', 'org.projectluma.Phone.desktop',
    'org.projectluma.Charlie.desktop', 'org.projectluma.Maps.desktop',
    'org.projectluma.Weather.desktop', 'org.projectluma.Clock.desktop',
    'org.projectluma.VoiceMemos.desktop', 'org.projectluma.Camera.desktop',
    'io.luma.Monitor.desktop', 'org.projectluma.Leaf.desktop',
    'org.projectluma.Depot.desktop', 'org.projectluma.Terminal.desktop',
    'org.projectluma.Viewer.desktop', 'org.gnome.Settings.desktop',
]


def command(*arguments):
    return subprocess.check_output(arguments, text=True, timeout=10).strip()


def favorites():
    # Each read opens a fresh process, rather than trusting an in-memory value.
    return ast.literal_eval(command('gsettings', 'get', SCHEMA, KEY))


def inner():
    if favorites() != EXPECTED:
        raise AssertionError(f'packaged dock defaults differ: {favorites()}')
    if command('gsettings', 'writable', SCHEMA, KEY) != 'true':
        raise AssertionError('dock favorites must remain an unlocked user preference')
    print('PASS native compiled dock: exact23, Filer/Viola/Ari first, Viewer/Settings last', flush=True)
    personal = ['viola-browser.desktop', 'org.gnome.Nautilus.desktop']
    subprocess.run(['gsettings', 'set', SCHEMA, KEY, repr(personal)], check=True, timeout=10)
    for _ in range(2):
        if favorites() != personal:
            raise AssertionError('reopening settings overwrote the personal dock order')
    print('PASS native dock: explicit personal favorites persist across reopened processes', flush=True)
    subprocess.run(['gsettings', 'reset', SCHEMA, KEY], check=True, timeout=10)
    if favorites() != EXPECTED:
        raise AssertionError('reset must restore the compiled23 default')
    print('PASS native dock: reset restores defaults without locks or login replay', flush=True)


if sys.argv[1:] == ['--inner']:
    inner()
else:
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_dock_defaults.py COMPILED_DCONF_DB')
    database = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='luma-dock-defaults-') as temporary:
        root = Path(temporary)
        runtime = root / 'runtime'; runtime.mkdir(mode=0o700)
        profile = root / 'profile'
        profile.write_text(f'user-db:user\nfile-db:{database}\n')
        environment = dict(os.environ, GSETTINGS_BACKEND='dconf', DCONF_PROFILE=str(profile),
                           XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'),
                           XDG_RUNTIME_DIR=str(runtime))
        environment.pop('DBUS_SESSION_BUS_ADDRESS', None)
        subprocess.run(['dbus-run-session', '--', sys.executable, __file__, '--inner'],
                       check=True, timeout=60, env=environment)
