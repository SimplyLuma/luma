#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare Python caches that survive OSTree's normalized source timestamps.

Run with the image's interpreter, before exporting the image. Never import
applications or execute their initialization. Hash-checked caches also remain
correct if a development overlay later changes a module's source.
"""
import argparse
import importlib.util
from pathlib import Path
import py_compile
import sysconfig


def prepare(roots):
    count = 0
    for root in roots:
        for source in sorted(Path(root).rglob('*.py')):
            if source.is_symlink() or any(p in {'test', 'tests', '__pycache__'} for p in source.parts):
                continue
            for optimization in ('', '1', '2'):
                cache = Path(importlib.util.cache_from_source(str(source), optimization=optimization))
                if optimization and not cache.exists():
                    continue
                py_compile.compile(str(source), cfile=str(cache), doraise=True,
                    optimize=int(optimization or 0), invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
                count += 1
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='*')
    args = parser.parse_args()
    roots = args.roots or list(dict.fromkeys(sysconfig.get_path(k) for k in ('stdlib', 'purelib')))
    print(f'Prepared {prepare(roots)} hash-checked Python caches')
