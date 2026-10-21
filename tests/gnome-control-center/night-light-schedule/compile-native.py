#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Link the schedule GUI matrix against a completed normal Settings build.

Usage: compile-native.py PREPARED_SOURCE MESON_BUILD OUTPUT_DIRECTORY
Run with the compiler/dependencies used for that normal build. Execute the
result separately against the actual packaged Luma toolkit.
"""
from pathlib import Path
import os
import platform
import shlex
import subprocess
import sys

source, build, output = (Path(arg).resolve() for arg in sys.argv[1:])
assert (source / 'shell/test-luma-view.c').is_file()
assert (build / 'build.ninja').is_file()
output.mkdir(parents=True, exist_ok=True)
for key, value in {'RPM_ARCH': platform.machine(),
                   'RPM_PACKAGE_NAME': 'gnome-control-center',
                   'RPM_PACKAGE_VERSION': '50.4',
                   'RPM_PACKAGE_RELEASE': 'native-schedule-check'}.items():
    os.environ.setdefault(key, value)
commands = subprocess.check_output(
    ['ninja', '-t', 'commands', 'shell/test-luma-view'], cwd=build, text=True).splitlines()
command = next(shlex.split(line) for line in commands
               if '../shell/test-luma-view.c' in line and ' -c ' in line)
command[command.index('-o') + 1] = str(output / 'native-view.o')
command[command.index('-MF') + 1] = str(output / 'native-view.d')
command[command.index('-c') + 1] = str(Path(__file__).with_name('test-native-view.c'))
command = [value for value in command if not value.startswith('-DLUMA_SHELL_SRCDIR=')]
command[1:1] = ['-I' + str(source / 'shell'),
                '-DLUMA_SHELL_SRCDIR="' + str(source / 'shell') + '"']
command = ['-flto=2' if value == '-flto=auto' else value for value in command]
subprocess.run(command, cwd=build, check=True)
command = shlex.split(commands[-1])
command[command.index('-o') + 1] = str(output / 'test-night-light')
command = [str(output / 'native-view.o')
           if value == 'shell/test-luma-view.p/test-luma-view.c.o' else value
           for value in command]
command = ['-flto=2' if value == '-flto=auto' else value for value in command]
subprocess.run(command, cwd=build, check=True)
