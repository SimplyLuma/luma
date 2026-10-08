#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sequential isolated host startup reproduction; invoked by approved queue.

Creates disposable before/after overlays, never activates them, and never
connects to Nick's session bus or changes his saved settings.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import importlib.util

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline-result', type=Path, help='reuse the preserved native before-top-center result')
parser.add_argument('--startup-only', action='store_true', help='complete the startup check independently of wider method checks')
parser.add_argument('--methods-only', action='store_true', help='run native method checks without repeating startup evidence')
parser.add_argument('--tests', nargs='+', help='specific owned native checks to run')
args = parser.parse_args()
here = Path(__file__).resolve().parent
stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
out = Path('/var/home/nick/Documents/LumaDesign/reports/notifications-quick-options') / ('startup-0205-' + stamp)
out.mkdir(parents=True)
if not args.methods_only:
    subprocess.run(['python3', str(here / 'stage-startup.py'), '--suffix', stamp], check=True)
results = {}
for label, scenario, overlay in [
    ('before-top-center', 'startup-top-center.json', 'notifications-startup-before-0205'),
    ('after-top-center', 'startup-top-center.json', 'notifications-startup-0205'),
    ('after-no-arrangement', 'startup-no-arrangement.json', 'notifications-startup-0205'),
]:
    if args.methods_only: continue
    if label == 'before-top-center' and args.baseline_result:
        prior = json.loads(args.baseline_result.read_text())['before-top-center']
        receipt = prior.get('receipt') or {}
        if prior['returncode'] == 0 or receipt.get('responsive') or not receipt.get('startup_cpu'):
            raise RuntimeError('baseline receipt does not evidence the reported startup failure')
        if 'org/project-luma/shell-state' not in receipt.get('settings', {}):
            raise RuntimeError('baseline receipt has the wrong saved-arrangement path')
        results[label] = dict(prior, preserved_evidence=str(args.baseline_result))
        print('PRESERVED NATIVE BASELINE', args.baseline_result, flush=True)
        continue
    target = out / label
    command = ['python3', str(here / 'capture.py'), str(here / scenario), 'dark', '0',
               str(target), str(Path.home() / '.local/share/luma-shell-live' / (overlay + '-' + stamp))]
    print('ISOLATED STARTUP', label, flush=True)
    result = subprocess.run(command, text=True, capture_output=True)
    print(result.stdout, end='', flush=True)
    print(result.stderr, end='', flush=True)
    target.mkdir(exist_ok=True)
    (target / 'probe.stdout').write_text(result.stdout)
    (target / 'probe.stderr').write_text(result.stderr)
    receipt = target / 'startup.json'
    results[label] = {'returncode': result.returncode,
                      'receipt': json.loads(receipt.read_text()) if receipt.exists() else None}
    (out / 'RESULTS.json').write_text(json.dumps(results, indent=2) + '\n')
    if label == 'before-top-center':
        observed = results[label]['receipt'] or {}
        if result.returncode == 0 or observed.get('responsive') or not observed.get('startup_cpu'):
            raise RuntimeError(f'negative startup control not reproduced (including guard skips): evidence at {out}')
    if label.startswith('after') and result.returncode:
        raise RuntimeError(f'fixed startup probe failed: {label}; evidence at {out}')

if args.startup_only:
    print('VERIFIED STARTUP EVIDENCE', out, flush=True)
    raise SystemExit(0)

# GJS parsing and actual-method tests use the host's actual GJS runtime.
# Private brokers isolate both buses; no Gtk/Clutter actors are instantiated.
module_spec = importlib.util.spec_from_file_location('private_capture', here / 'capture.py')
private_capture = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(private_capture)
with tempfile.TemporaryDirectory(prefix='luma-startup-method-tests.') as private:
    runtime_env = {k: v for k, v in os.environ.items() if k not in
                   ['DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'DBUS_SYSTEM_BUS_ADDRESS']
                   and not k.startswith('XDG_')}
    for kind in ['config', 'data', 'cache', 'state', 'runtime']:
        path = Path(private) / kind; path.mkdir(mode=0o700)
        key = 'XDG_RUNTIME_DIR' if kind == 'runtime' else f'XDG_{kind.upper()}_HOME'
        runtime_env[key] = str(path)
    runtime_env.update(GSETTINGS_BACKEND='memory', GDK_BACKEND='x11')
    config = Path(private) / 'bus.conf'; config.write_text(private_capture.BUS_CONF)
    processes = []
    with open(out / 'method-buses.log', 'w') as bus_log:
        try:
            for name in ['bus', 'sysbus']:
                address = f'unix:path={private}/{name}'
                bus_env = dict(runtime_env, DBUS_SESSION_BUS_ADDRESS=address)
                processes.append(subprocess.Popen([
                    'systemd-socket-activate', '-E', 'DBUS_SESSION_BUS_ADDRESS', '-E', 'XDG_RUNTIME_DIR',
                    '-l', f'{private}/{name}', 'dbus-broker-launch', '--scope=user', f'--config-file={config}'],
                    env=bus_env, stdout=bus_log, stderr=bus_log, start_new_session=True))
            time.sleep(.3)
            runtime_env.update(DBUS_SESSION_BUS_ADDRESS=f'unix:path={private}/bus',
                               DBUS_SYSTEM_BUS_ADDRESS=f'unix:path={private}/sysbus')
            source = '/var/home/nick/Documents/.luma-dev/notifications-quick-options/edited'
            for test in ['behavior.js', 'allocation-behavior.js', 'network-behavior.js',
                         'reply-behavior.js', 'system-behavior.js', 'output-behavior.js',
                         'power-title-behavior.js', 'native-hotspot-profile.js']:
                if args.tests and test not in args.tests: continue
                command = ['/usr/bin/gjs', str(here / test), source]
                if test in ['behavior.js', 'reply-behavior.js', 'system-behavior.js', 'output-behavior.js']:
                    command.append('--self-test')
                print('ISOLATED HOST GJS', test, flush=True)
                try:
                    result = subprocess.run(command, env=runtime_env, text=True, capture_output=True, timeout=60)
                except subprocess.TimeoutExpired as error:
                    stdout = error.stdout.decode(errors='replace') if isinstance(error.stdout, bytes) else error.stdout or ''
                    stderr = error.stderr.decode(errors='replace') if isinstance(error.stderr, bytes) else error.stderr or ''
                    (out / f'{test}.stdout').write_text(stdout)
                    (out / f'{test}.stderr').write_text(stderr)
                    print(stdout, end='', flush=True); print(stderr, end='', flush=True)
                    results[test] = {'returncode': None, 'timeout': 60}
                    (out / 'RESULTS.json').write_text(json.dumps(results, indent=2) + '\n')
                    raise
                print(result.stdout, end='', flush=True); print(result.stderr, end='', flush=True)
                (out / f'{test}.stdout').write_text(result.stdout)
                (out / f'{test}.stderr').write_text(result.stderr)
                results[test] = {'returncode': result.returncode}
                (out / 'RESULTS.json').write_text(json.dumps(results, indent=2) + '\n')
                if result.returncode: raise RuntimeError(f'GJS test failed: {test}; evidence at {out}')
        finally:
            for process in reversed(processes): private_capture.stop(process)
print('EVIDENCE', out, flush=True)
print('Native startup probes and GJS method checks completed; inspect CPU receipts and full logs.', flush=True)
