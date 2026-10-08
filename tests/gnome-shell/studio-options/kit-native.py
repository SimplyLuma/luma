#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run actual shared kit tests on a private X11 display and session bus.

The toolbox is resolved with its normal host environment. All XDG, bus,
display and settings isolation is applied inside the final toolbox runtime.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--tree', type=Path, default=Path(__file__).resolve().parents[3])
p.add_argument('--out', type=Path, required=True)
p.add_argument('--pattern', default='test_luma_appkit*.py')
a = p.parse_args()
if a.out.exists():
    raise RuntimeError('refusing to overwrite kit evidence')
a.out.mkdir(parents=True)
runtime = '''import json, os, subprocess, sys, unittest
from pathlib import Path
# Keep the no-GTK agent assertion meaningful: inspect the display in a
# separate process rather than importing Gtk into unittest discovery.
os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = os.environ['DBUS_SESSION_BUS_ADDRESS']
probe = "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk, Gdk; assert Gtk.init_check() and Gdk.Display.get_default(), 'actual private GTK display required'; print('PRIVATE GTK DISPLAY', Gdk.Display.get_default().get_name(), flush=True)"
subprocess.run([sys.executable, '-c', probe], check=True)
tree, output = map(Path, sys.argv[1:3])
suite = unittest.defaultTestLoader.discover(str(tree / 'tests/unit'), pattern=sys.argv[3])
assert suite.countTestCases() > 0, 'kit discovery must execute actual tests'
result = unittest.TextTestRunner(verbosity=2).run(suite)
receipt = {'testsRun': result.testsRun, 'failures': [test.id() for test, _ in result.failures],
           'errors': [test.id() for test, _ in result.errors],
           'skips': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
           'successful': result.wasSuccessful(), 'tree': str(tree)}
(output / 'RESULTS.json').write_text(json.dumps(receipt, indent=2) + '\\n')
assert result.testsRun > 0, 'empty kit run is not success'
sys.exit(0 if result.wasSuccessful() else 1)
'''
with tempfile.TemporaryDirectory(prefix='luma-owned-kit-native.') as private:
    isolation = []
    for kind in ['config', 'data', 'cache', 'state', 'runtime']:
        directory = Path(private) / kind
        directory.mkdir(mode=0o700)
        key = 'XDG_RUNTIME_DIR' if kind == 'runtime' else f'XDG_{kind.upper()}_HOME'
        isolation.append(f'{key}={directory}')
    command = ['toolbox', 'run', '-c', 'luma-dev-f44', 'env',
               '-u', 'DISPLAY', '-u', 'WAYLAND_DISPLAY',
               '-u', 'DBUS_SESSION_BUS_ADDRESS', '-u', 'DBUS_SYSTEM_BUS_ADDRESS',
               *isolation, 'GDK_BACKEND=x11', 'GSETTINGS_BACKEND=memory', 'PYTHONDONTWRITEBYTECODE=1',
               'xvfb-run', '-a', 'dbus-run-session', '--',
               'python3', '-c', runtime, str(a.tree.resolve()), str(a.out.resolve()), a.pattern]
    (a.out / 'COMMAND.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, text=True, capture_output=True, timeout=300)
    for key, content in [('stdout', result.stdout), ('stderr', result.stderr)]:
        (a.out / f'{key}.log').write_text(content)
        print(content, end='', flush=True)
    if not (a.out / 'RESULTS.json').is_file():
        raise RuntimeError(f'kit runtime produced no test receipt; returncode={result.returncode}')
    raise SystemExit(result.returncode)
