#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the Meson selected by Mesa's own host PATH before compiling Android."""
import os
from pathlib import Path
import re
import subprocess
import sys


def verify(source, inherited_path):
    declaration = (Path(source) / 'external/mesa/meson.build').read_text()
    match = re.search(r"meson_version\s*:\s*['\"]>=\s*(\d+(?:\.\d+)+)['\"]", declaration)
    if not match:
        raise RuntimeError('Cannot determine pinned Mesa Meson requirement')
    required = match[1]
    # Keep this identical to external/mesa/android/mesa3d_cross.mk. Checking
    # only the inherited PATH can pass while Mesa later selects /usr/bin/meson.
    env = dict(os.environ, PATH='/usr/bin:/usr/local/bin:' + inherited_path)
    # Soong drops PYTHONPATH before executing Ninja. A launcher that imports a
    # newer Meson only through that variable would pass here and fail in Mesa.
    env.pop('PYTHONPATH', None)
    actual = subprocess.check_output(['meson', '--version'], env=env, text=True, timeout=15).strip()
    if not re.fullmatch(r'\d+(?:\.\d+)+', actual):
        raise RuntimeError('Cannot determine selected Meson version: ' + actual)
    version = lambda text: tuple(int(part) for part in text.split('.'))
    if version(actual) < version(required):
        raise RuntimeError(f'Mesa selects Meson {actual}; requires >= {required}. Update the selected host tool before building.')
    print(f'Mesa host tool verified: Meson {actual} (requires >= {required})', flush=True)
    return actual


def verify_glslang(source, inherited_path):
    declaration = (Path(source) / 'external/mesa/meson.build').read_text()
    match = re.search(r"_glslang_preamble_version\s*=\s*['\"](\d+(?:\.\d+)+)['\"]", declaration)
    if not match:
        raise RuntimeError('Cannot determine pinned Mesa glslang requirement')
    required = match[1]
    env = dict(os.environ, PATH='/usr/bin:/usr/local/bin:' + inherited_path)
    env.pop('PYTHONPATH', None)
    output = subprocess.check_output(['glslangValidator', '--version'], env=env, text=True, timeout=15)
    match = re.search(r'Glslang Version:\s*\d+:(\d+(?:\.\d+)+)', output)
    if not match:
        raise RuntimeError('Cannot determine selected glslang version')
    actual = match[1]
    version = lambda text: tuple(int(part) for part in text.split('.'))
    if version(actual) < version(required):
        raise RuntimeError(f'Mesa selects glslang {actual}; requires >= {required}. Update the selected host tool before building.')
    print(f'Mesa host tool verified: glslang {actual} (requires >= {required})', flush=True)
    return actual


if __name__ == '__main__':
    try:
        verify(sys.argv[1], os.environ['PATH'])
        verify_glslang(sys.argv[1], os.environ['PATH'])
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit('error: ' + str(error))
