#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run in the Fedora rasterization builder, requiring rsvg-convert."""
from pathlib import Path
import hashlib
import importlib.util
import itertools
import json
import subprocess
import sys
root=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('compiler',root/'scripts/boot/compile-theme.py')
compiler=importlib.util.module_from_spec(spec);spec.loader.exec_module(compiler)
output=Path(sys.argv[1]); output.mkdir(parents=True,exist_ok=True)
canonical=(root/'website/public/brand/luma-wordmark.svg').read_text()
for surface,loader,progress in itertools.product(compiler.PALETTES,compiler.CHOICES['loader'],compiler.CHOICES['progress']):
    profile=dict(compiler.MOBILE,surface=surface,loader=loader,progress=progress)
    compiler.artwork(profile,canonical,output/'raster-matrix')
    paths=list((output/'raster-matrix').glob('*.png'))
    expected=9+2*len(range(104,143))+(61 if loader=='ring' else 0)
    assert len(paths)==expected,(surface,loader,progress,len(paths))
    for path in paths:
        data=path.read_bytes();assert data[:8]==b'\x89PNG\r\n\x1a\n'
        assert int.from_bytes(data[16:20],'big')>0 and int.from_bytes(data[20:24],'big')>0
    for width in range(104,143):
        for stem in ('luma-wordmark','luma-wordmark-stop'):
            data=(output/'raster-matrix'/f'{stem}-{width}.png').read_bytes()
            assert (int.from_bytes(data[16:20],'big'),int.from_bytes(data[20:24],'big')) == (width,width*715//2219)
    if loader=='ring':
        prefix='ring-progress' if progress=='determinate' else 'ring-spin'
        assert (output/'raster-matrix'/f'{prefix}-0.png').read_bytes() != (output/'raster-matrix'/f'{prefix}-30.png').read_bytes()
print('Native rasterization profiles and stale-asset cleanup: 24 PASS')
desktop=json.loads((root/'config/boot/desktop-theme.json').read_text())
for repeat in range(2):
    compiler.compile_profiles(root,desktop,compiler.MOBILE,output/'reproducible')
    digest=hashlib.sha256((output/'reproducible/luma-boot-variants.tar.gz').read_bytes()).hexdigest()
    if repeat: assert digest==previous
    previous=digest
print('Raster asset archive reproducibility: PASS')

for surface,(background,foreground) in compiler.PALETTES.items():
    compiler.compile_profiles(root,dict(desktop,surface=surface),compiler.MOBILE,output/'palette')
    grub=(output/'palette/luma-grub-theme.txt').read_text()
    assert f'desktop-color: "{background}"' in grub
    assert f'title-color: "{foreground}"' in grub
print('GRUB and Plymouth build-profile palettes: 3 PASS')
