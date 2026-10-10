#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile actual native actions from a configured Settings source/build tree."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True, help='Settings shell directory')
parser.add_argument('--build', type=Path, required=True, help='Configured Settings Meson build')
args = parser.parse_args()
source, build = args.source.resolve(), args.build.resolve()
commands = json.loads((build / 'compile_commands.json').read_text())

with tempfile.TemporaryDirectory(prefix='luma-settings-native-actions-') as directory:
    for test, production in (('test-cache.c', 'cc-luma-view-application-options.c'),
                             ('test-app-launch.c', 'cc-luma-view-application-options.c'),
                             ('test-manual-time.c', 'cc-luma-live-datetime.c')):
        row = next(item for item in commands if item['file'].endswith('/' + production))
        command = shlex.split(row['command'])
        if command[0].endswith('ccache'):
            command.pop(0)
        for flag in ('-MD', '-MQ', '-MF', '-o', '-c'):
            if flag in command:
                index = command.index(flag)
                del command[index:index + (1 if flag in ('-MD', '-c') else 2)]
        command[-1] = str(Path(__file__).resolve().parent / test)
        command.insert(1, '-I' + str(source))
        binary = str(Path(directory) / test.removesuffix('.c'))
        command += [f'-DSETTINGS_ACTION_SOURCE="{source / production}"',
                    '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                    '-o', binary]
        command += shlex.split(subprocess.check_output(
            ['pkg-config', '--libs', 'gtk4', 'libadwaita-1', 'json-glib-1.0', 'gio-unix-2.0'], text=True))
        subprocess.run(command, cwd=build, check=True)
        invocation = [binary]
        environment = dict(os.environ, GSK_RENDERER='cairo', GTK_A11Y='none')
        if test == 'test-app-launch.c':
            invocation = ['dbus-run-session', '--', 'xvfb-run', '-a', binary]
        subprocess.run(invocation, env=environment, check=True)
