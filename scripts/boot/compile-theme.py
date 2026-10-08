#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate image-owned boot profiles and compile native Plymouth assets.

No user settings, network access, or initramfs mutation. Changing a profile
requires a new package and a normal composed initramfs/deployment.
"""
from __future__ import annotations
import argparse
import json
import gzip
import importlib.util
import os
import re
from pathlib import Path
import subprocess
import tarfile
import xml.etree.ElementTree as ET

CHOICES = {'concept': ('hearth','paper'), 'surface': ('ink','paper','slate'),
           'loader': ('ring','bar','hairline','dots'),
           'lockup': ('stacked','inline','word','mark'),
           'progress': ('determinate','indeterminate')}
PALETTES = {'ink': ('#21252b','#f2f3f4'), 'paper': ('#f2f3f4','#21252b'),
            'slate': ('#343a42','#f2f3f4')}
# Secondary glyphs (the Caps Lock indicator) on each surface.
MUTED = {'ink': '#bfc3c8', 'paper': '#687078', 'slate': '#bfc3c8'}
PROMPT_PIECES = {'entry': 'prompt-field.png', 'bullet': 'prompt-bullet.png',
                 'lock': 'prompt-lock.png', 'capslock': 'prompt-capslock.png'}
MOBILE = dict(concept='hearth',surface='paper',loader='dots',lockup='word',progress='indeterminate')

def validate(profile, handheld=False):
    if not isinstance(profile,dict) or profile.keys() != CHOICES.keys():
        raise ValueError('profile must specify exactly: '+', '.join(CHOICES))
    for key, choices in CHOICES.items():
        if profile[key] not in choices:
            raise ValueError(f'{key} must be one of {choices}')
    if handheld and profile != MOBILE:
        raise ValueError('handheld presentation is the approved Hearth/Paper/dots/word activity profile')
    return profile

def script(profile, template, handheld=False):
    validate(profile,handheld)
    bg,ink=PALETTES[profile['surface']]
    lines=['// Generated from the shared renderer and validated build profile.']
    for prefix,color in [('background',bg),('ink',ink)]:
        for channel,start in [('red',1),('green',3),('blue',5)]:
            lines.append(f'global.{prefix}_{channel} = {int(color[start:start+2],16)/255:.8f};')
    for key in ('concept','loader','lockup'):
        lines.append(f'global.{key} = {json.dumps(profile[key])};')
    lines += [f'global.progress_mode = {json.dumps(profile["progress"])};',
              f'global.handheld = {1 if handheld else 0};']
    return '\n'.join(lines)+'\n'+template

def raster(svg, output, width, height):
    subprocess.run(['rsvg-convert',f'--width={width}',f'--height={height}',
                    f'--output={output}'],input=svg.encode(),check=True)

def artwork(profile, canonical, directory, handheld=False):
    directory.mkdir(parents=True,exist_ok=True)
    # A profile switch must not keep assets from an earlier ring variant.
    for previous in directory.glob('*.png'):
        if previous.name in {'luma-wordmark.png','luma-wordmark-stop.png','luma-loading-dot.png','luma-track.png','luma-mark.png',*PROMPT_PIECES.values()} or re.fullmatch(r'(?:ring-(?:progress|spin)|luma-wordmark(?:-stop)?)-[0-9]+\.png', previous.name):
            previous.unlink()
    _,ink=PALETTES[profile['surface']]
    # Preserve exact geometry, separating the existing orange stop path.
    for stop in (False,True):
        tree=ET.fromstring(canonical)
        for group in list(tree):
            for child in list(group):
                if child.tag.endswith('path'):
                    orange=child.get('fill') != 'currentColor'
                    if orange != stop: group.remove(child)
                    elif not orange: child.set('fill',ink)
        name='luma-wordmark-stop.png' if stop else 'luma-wordmark.png'
        vector=ET.tostring(tree,encoding='unicode')
        raster(vector,directory/name,2219,715)
        # Plymouth's bilinear image scaler samples rather than integrates
        # coverage. Shrinking a 2219 px raster to 104–142 px loses edge
        # antialiasing. Render the same SVG at each existing drawn size;
        # no geometry, colours, or wordmark are redesigned here.
        widths=range(120,481) if handheld else range(104,143)
        stem=Path(name).stem
        for width in widths:
            raster(vector,directory/f'{stem}-{width}.png',width,width*715//2219)
    def svg(body,box='0 0 128 128'):
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}">{body}</svg>'
    raster(svg(f'<circle cx="64" cy="64" r="64" fill="{ink}"/>'),directory/'luma-loading-dot.png',128,128)
    raster(svg(f'<path fill="{ink}" d="M0 0H128V128H0Z"/>'),directory/'luma-track.png',1,1)
    # Direct translation of the simulator's 1.66em x 1em capsule mark,
    # .145em border; this does not replace or redraw the canonical wordmark.
    raster(svg(f'<rect x="3.045" y="3.045" width="63.63" height="35.91" rx="17.955" '
               f'fill="none" stroke="{ink}" stroke-width="6.09"/>','0 0 69.72 42'),directory/'luma-mark.png',280,168)
    # The unlock prompt, drawn with the same geometry as luma-firmware's and
    # coloured for this surface: the field in the ink colour, bullets in the
    # surface colour.
    renderer=importlib.util.spec_from_file_location('render_firmware_theme',
                                                    Path(__file__).resolve().parent/'render-firmware-theme.py')
    firmware=importlib.util.module_from_spec(renderer); renderer.loader.exec_module(firmware)
    background,_=PALETTES[profile['surface']]
    firmware.prompt(directory,ink=ink,surface=background,muted=MUTED[profile['surface']],names=PROMPT_PIECES)
    if profile['loader']=='ring':
        mode=profile['progress']
        for frame in range(61):
            fraction=frame/60 if mode=='determinate' else .75
            rotation=-90 if mode=='determinate' else frame*6-90
            body=(f'<circle cx="64" cy="64" r="59" fill="none" stroke="{ink}" stroke-opacity=".16" stroke-width="10"/>'
                  f'<circle cx="64" cy="64" r="59" pathLength="100" fill="none" stroke="{ink}" stroke-width="10" '
                  f'stroke-dasharray="{fraction*100} {100-fraction*100}" transform="rotate({rotation} 64 64)"/>')
            prefix='ring-progress' if mode=='determinate' else 'ring-spin'
            raster(svg(body),directory/f'{prefix}-{frame}.png',128,128)

def compile_profiles(root,desktop,mobile,output,rasterize=True):
    template=(root/'assets/boot/luma-theme.script.in').read_text()
    output.mkdir(parents=True,exist_ok=True)
    for name,profile,handheld in [('luma-loading',desktop,False),('luma-loading-handheld',mobile,True)]:
        (output/f'{name}.script').write_text(script(profile,template,handheld))
        if rasterize:
            artwork(profile,(root/'website/public/brand/luma-wordmark.svg').read_text(),output/'variant-assets'/name,handheld)
    # Keep the pre-kernel theme's palette coupled to the selected desktop profile.
    bg,ink=PALETTES[desktop['surface']]
    grub=(root/'assets/boot/grub-theme/theme.txt').read_text()
    grub=grub.replace('#21252b','@BACKGROUND@').replace('#f2f3f4','@INK@')
    grub=grub.replace('@BACKGROUND@',bg).replace('@INK@',ink)
    if desktop['surface']=='paper':
        grub=grub.replace('#c2c5c8','#687078').replace('#9ca3af','#687078').replace('#484c52','#d1d4d8')
    if rasterize: (output/'luma-grub-theme.txt').write_text(grub)
    if rasterize:
        # Archive members are a fixed pair of package-owned theme directories.
        with (output/'luma-boot-variants.tar.gz').open('wb') as raw:
            with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as compressed:
                with tarfile.open(fileobj=compressed,mode='w') as tar:
                    for path in sorted((output/'variant-assets').rglob('*.png')):
                        info=tar.gettarinfo(str(path),arcname=str(path.relative_to(output/'variant-assets')))
                        info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0;info.mode=0o644
                        with path.open('rb') as data: tar.addfile(info,data)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--desktop-profile',type=Path)
    parser.add_argument('--mobile-profile',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--scripts-only',action='store_true')
    args=parser.parse_args()
    desktop=json.loads((args.desktop_profile or args.root/'config/boot/desktop-theme.json').read_text())
    mobile=json.loads((args.mobile_profile or args.root/'config/mobile/boot-theme.json').read_text())
    compile_profiles(args.root,validate(desktop),validate(mobile,True),args.output,not args.scripts_only)
