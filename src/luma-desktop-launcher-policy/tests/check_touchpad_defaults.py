#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read the actual compiled policy DB with native GSettings/dconf in isolation.

Validate supported schema values, actual defaults, writability and persistent
user-preference precedence. This does not claim physical clickpad acceptance.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SCHEMA = 'org.gnome.desktop.peripherals.touchpad'
EXPECTED = {'tap-to-click': 'true', 'tap-button-map': "'lrm'", 'click-method': "'areas'"}

def command(*arguments):
    return subprocess.check_output(arguments, text=True, timeout=10).strip()

def inner():
    for key, expected in EXPECTED.items():
        actual = command('gsettings', 'get', SCHEMA, key)
        if actual != expected:
            raise AssertionError(f'{key}: expected packaged default {expected}, got {actual}')
        if command('gsettings', 'writable', SCHEMA, key) != 'true':
            raise AssertionError(f'{key} must remain a user preference')
        print(f'PASS native policy default: {key}={actual}, writable', flush=True)
    accepted = command('gsettings', 'range', SCHEMA, 'click-method')
    if not accepted.startswith('enum\n') or "'areas'" not in accepted.splitlines():
        raise AssertionError(f'areas not supported by actual GNOME schema: {accepted}')
    if "'lrm'" not in command('gsettings', 'range', SCHEMA, 'tap-button-map').splitlines():
        raise AssertionError('lrm not supported by actual GNOME schema')
    for key, personal in (('click-method', "'fingers'"), ('tap-to-click', 'false'),
                          ('tap-button-map', "'lmr'")):
        subprocess.run(['gsettings', 'set', SCHEMA, key, personal], check=True, timeout=10)
        actual = command('gsettings', 'get', SCHEMA, key)
        if actual != personal:
            raise AssertionError(f'{key}: explicit user value {personal} was overwritten by default {actual}')
        print(f'PASS native user precedence: {key}={actual} persists across processes', flush=True)
    for key, expected in EXPECTED.items():
        subprocess.run(['gsettings', 'reset', SCHEMA, key], check=True, timeout=10)
        if command('gsettings', 'get', SCHEMA, key) != expected:
            raise AssertionError(f'{key}: resetting user choice must restore packaged default')
    print('PASS native policy: reset restores compiled defaults; no locks or replay', flush=True)

if sys.argv[1:] == ['--inner']:
    inner()
else:
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_touchpad_defaults.py COMPILED_DCONF_DB')
    database = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='luma-touchpad-defaults-') as temporary:
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
