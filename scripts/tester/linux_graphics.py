# SPDX-License-Identifier: Apache-2.0
"""Complete and validate the portable GTK graphics closure, including dlopen inputs."""
from pathlib import Path
import os
import re
import subprocess


def needed(path):
    data = subprocess.check_output(['readelf', '-d', str(path)], text=True)
    return re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', data)


def stage_ui_graphics(bundle):
    bundle = Path(bundle).resolve()
    ui, tools = bundle/'usr/lib64', bundle/'tools/lib'
    # Mesa's internal ABI is not stable. Keep its EGL, GBM, Gallium and DRI
    # providers from one build; retain the UI's own loader, libc, GTK and GLib.
    seeds = ['libEGL_mesa.so.0', 'libGLX_mesa.so.0', 'libGL.so.1', 'libGLESv2.so.2', 'libgbm.so.1']
    seeds += [p.name for p in sorted(tools.glob('libgallium-*.so'))]
    seeds += [p.name for p in sorted(tools.glob('libvulkan_*.so'))]
    if not list(tools.glob('libgallium-*.so')):
        raise RuntimeError('Portable graphics requires its matching Gallium implementation.')
    mesa = set(seeds)
    pending, checked, linked = list(seeds), set(), []
    while pending:
        name = pending.pop()
        if name in checked:
            continue
        checked.add(name)
        if Path(name).name != name:
            raise RuntimeError('Graphics dependency must have a local SONAME: '+name)
        target = ui/name
        if target.exists() and name not in mesa:
            # Its dependencies already belong to the qualified UI closure.
            continue
        source = tools/name
        if not source.is_file():
            raise RuntimeError('Missing private graphics dependency: '+name)
        if not source.resolve().is_relative_to(bundle):
            raise RuntimeError('Graphics dependency escapes the portable directory: '+name)
        target.unlink(missing_ok=True)
        target.symlink_to(os.path.relpath(source, ui))
        linked.append(name)
        pending.extend(needed(source))
    loader = ui/'ld-linux-x86-64.so.2'
    for name in linked:
        result = subprocess.run([str(loader), '--inhibit-cache', '--library-path', str(ui),
                                 '--list', str(ui/name)], capture_output=True, text=True)
        if result.returncode or 'not found' in result.stdout+result.stderr:
            raise RuntimeError('Graphics does not match the UI runtime: '+name+'\n'+result.stdout+result.stderr)
        for path in re.findall(r'=>\s+(/\S+)', result.stdout):
            if not Path(path).resolve().is_relative_to(bundle):
                raise RuntimeError('Graphics resolved a host dependency: '+path)
    return sorted(linked)
