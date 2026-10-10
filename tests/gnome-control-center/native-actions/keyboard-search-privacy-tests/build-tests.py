# SPDX-License-Identifier: GPL-2.0-or-later
"""Build native action regressions using matching Settings build objects."""
import json
import shlex
import subprocess
import sys
from pathlib import Path
source, build, output = (Path(arg).resolve() for arg in sys.argv[1:4])
output.mkdir(parents=True, exist_ok=True)
tests = Path(__file__).resolve().parent
entries = json.loads((build / 'compile_commands.json').read_text())
def compile_file(path, template, name):
    entry = next(e for e in entries if Path(e['file']).name == template)
    command = shlex.split(entry['command'])
    compiler = next(i for i,a in enumerate(command) if Path(a).name in ('cc', 'gcc'))
    command = command[compiler+1:]
    for flag in ('-MD', '-MMD', '-MP'):
        if flag in command:
            command.remove(flag)
    for flag in ('-MQ', '-MF', '-MT'):
        if flag in command:
            i = command.index(flag)
            del command[i:i+2]
    obj = output / (name + '.o')
    command[command.index('-o')+1] = str(obj)
    command[command.index('-c')+1] = str(path)
    subprocess.run(['gcc', '-I'+str(source/'shell'), *command], cwd=build, check=True)
    return obj
objects = {}
for name in ('cc-luma-live-keyboard', 'cc-luma-keyboard', 'cc-luma-discovery'):
    objects[name] = compile_file(source/'shell'/(name+'.c'), name+'.c', name)
for name, replaced in (('test-native-privacy', 'cc-luma-view-privacy'),
                       ('test-native-search-folders', 'cc-luma-view-discovery')):
    obj = compile_file(tests/(name+'.c'), 'test-luma-view.c', name)
    link = shlex.split(subprocess.check_output(
        ['ninja', '-C', str(build), '-t', 'commands', 'shell/test-luma-view'], text=True).splitlines()[-1])
    link[link.index('-o')+1] = str(output/name)
    link[link.index('shell/test-luma-view.p/test-luma-view.c.o')] = str(obj)
    link.remove('shell/test-luma-view.p/'+replaced+'.c.o')
    subprocess.run(link, cwd=build, check=True)
for name in ('test-native-keyboard', 'test-search-write-failure'):
    obj = compile_file(tests/(name+'.c'), 'test-luma-view.c', name)
    link = ['gcc', str(obj), 'shell/test-luma-view.p/cc-luma-fixture.c.o']
    if name == 'test-native-keyboard':
        link += [str(objects['cc-luma-live-keyboard']), str(objects['cc-luma-keyboard']),
                 'shell/test-luma-view.p/cc-luma-device-options.c.o',
                 'shell/test-luma-view.p/cc-luma-displays.c.o',
                 'shell/test-luma-live-desk.p/cc-luma-live-gsettings.c.o']
        packages = ['gtk4', 'json-glib-1.0', 'gnome-desktop-4', 'xkbcommon']
    else:
        link += [str(objects['cc-luma-discovery'])]
        packages = ['gtk4', 'json-glib-1.0']
    link += ['-lm', '-o', str(output/name)] + shlex.split(subprocess.check_output(
        ['pkg-config', '--libs', *packages], text=True))
    subprocess.run(link, cwd=build, check=True)
print('Native keyboard, search and privacy test binaries built')
