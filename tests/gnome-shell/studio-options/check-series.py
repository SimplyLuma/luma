#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate registration/release coherence and replay the actual spec series.

check-series.py --self-test [--source /clean/gnome-shell-50.3 --out /new/output]
The output must not exist. Does not build an RPM or replace its %check.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / 'patches/gnome-shell/0000-luma-fedora-spec.patch'
BUILD = REPO / 'scripts/packages/build-gnome-shell.sh'

def validate(spec, build):
    declarations = re.findall(r'^\+Patch(\d+):\s+(\S+)', spec, re.M)
    if len(declarations) < 180:
        raise ValueError(f'expected complete registered series, got {len(declarations)}')
    ids = [i for i, _ in declarations]
    if len(set(ids)) != len(ids): raise ValueError('duplicate Patch IDs')
    names = [n for _, n in declarations]
    if len(set(names)) != len(names): raise ValueError('duplicate patch file registration')
    for name in names:
        patch = REPO / 'patches/gnome-shell' / name
        if not patch.is_file() or '@@ ' not in patch.read_text(): raise ValueError(f'missing/empty source patch: {name}')
        if f'$repo_root/patches/gnome-shell/{name}' not in build: raise ValueError(f'missing build copy: {name}')
    release = re.search(r'^\+Release:\s+(\S+)%\{\?dist\}', spec, re.M)
    copied = re.search(r'^surface_candidate_release=(\S+)', build, re.M)
    if not release or not copied or release[1] != copied[1]: raise ValueError('spec/build release mismatch')
    return names, release[1]

def self_test(spec, build):
    cases = [(spec, build.replace('0202-luma-studio-card-reflow.patch', 'MISSING-COPY.patch')),
             (spec, re.sub(r'^surface_candidate_release=.*', 'surface_candidate_release=stale', build, flags=re.M)),
             ('', build)]
    for index, (broken_spec, broken_build) in enumerate(cases):
        try: validate(broken_spec, broken_build)
        except ValueError: continue
        raise RuntimeError(f'self-test missed fault {index}')
    print('SELF-TEST detected missing copy, stale release and empty series')

def main():
    p = argparse.ArgumentParser(description=__doc__);p.add_argument('--self-test', action='store_true');p.add_argument('--source', type=Path);p.add_argument('--out', type=Path);a=p.parse_args()
    spec, build = SPEC.read_text(), BUILD.read_text()
    if a.self_test:self_test(spec, build)
    names, release = validate(spec, build)
    subprocess.run(['bash', '-n', str(BUILD)], check=True)
    print(f'PASS {len(names)} registered patches; spec/build release {release}')
    if bool(a.source) != bool(a.out):p.error('--source and --out must be supplied together')
    if a.source:
        if a.out.exists():raise RuntimeError('output already exists; refusing to overwrite')
        shutil.copytree(a.source, a.out, symlinks=True)
        for name in names:
            r=subprocess.run(['patch','--batch','--forward','-p1','-i',str(REPO/'patches/gnome-shell'/name)],cwd=a.out,text=True,capture_output=True)
            if r.returncode:raise RuntimeError(f'{name}: {r.stdout[-3000:]} {r.stderr}')
        print(f'PASS replayed {len(names)} patches onto clean source at {a.out}')
    return 0

if __name__ == '__main__':raise SystemExit(main())
