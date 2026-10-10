# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile the input regression with an existing, matching Settings build."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

source, build, output = (Path(p).resolve() for p in sys.argv[1:4])
test_source = Path(__file__).with_name('test-input-view.c')
entries = json.loads((build / 'compile_commands.json').read_text())
entry = next(e for e in entries if Path(e['file']).name == 'test-luma-view.c')
command = shlex.split(entry['command'])
command = command[command.index('gcc') + 1:]
flags = []
i = 0
while i < len(command):
    arg = command[i]
    if arg in ('-o', '-c', '-MF', '-MQ', '-MT'):
        i += 2
        continue
    if arg in ('-MD', '-MMD', '-MP'):
        i += 1
        continue
    flags.append(arg)
    i += 1
flags = ['-I' + str(build)] + flags
flags += ['-I' + str(source / 'shell'), '-I' + str(source / 'subprojects/gvc'),
          '-I' + str(build / 'subprojects/gvc')]
flags += shlex.split(subprocess.check_output(['pkg-config', '--cflags', 'libpulse'], text=True))
obj = output.with_suffix('.o')
subprocess.run(['gcc', *flags, '-c', str(test_source), '-o', str(obj)], cwd=build, check=True)
link = shlex.split(subprocess.check_output(
    ['ninja', '-C', str(build), '-t', 'commands', 'shell/test-luma-view'], text=True).splitlines()[-1])
link[link.index('-o') + 1] = str(output)
link[link.index('shell/test-luma-view.p/test-luma-view.c.o')] = str(obj)
link += ['shell/test-luma-live-desk.p/cc-luma-live-gsettings.c.o',
         'subprojects/gvc/libgvc.a', '-lpulse', '-lpulse-mainloop-glib', '-lz']
subprocess.run(link, cwd=build, check=True)
