#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Adapt the installed, pinned Lorax templates at their native build boundary."""
import shutil
import sys
from pathlib import Path

THEME = '''
# Project Luma: the same source-owned fonts and theme as the installed OS.
insmod gfxterm
insmod gfxmenu
loadfont /boot/grub2/themes/luma/prairie-12.pf2
loadfont /boot/grub2/themes/luma/prairie-22.pf2
if loadfont /boot/grub2/themes/luma/prairie.pf2 && [ "${timeout_style}" != "hidden" ]; then
  terminal_output gfxterm
fi
set theme=/boot/grub2/themes/luma/theme.txt
'''
SEARCH = "search --no-floppy --set=root -l '@ISOLABEL@'"
ARCH = '## configure grub2 config file\nmkdir ${GRUB2DIR}\n'
ASSETS = '''mkdir ${GRUB2DIR}/themes/luma
install usr/share/luma/boot/grub-theme/* ${GRUB2DIR}/themes/luma/
'''

def prepare(source, destination):
    generic = source / 'templates.d/99-generic'
    changes = {}
    for kind in ('bios', 'efi'):
        path = generic / f'config_files/x86/grub2-{kind}.cfg'
        text = path.read_text()
        for marker in (SEARCH, 'set default="1"', 'set timeout=60'):
            if text.count(marker) != 1:
                raise ValueError(f'{path}: unsupported Lorax boot layout: {marker}')
        if 'inst.rescue quiet' not in text or 'nomodeset quiet' not in text:
            raise ValueError(f'{path}: missing recovery/basic-graphics choices')
        text = text.replace(SEARCH, SEARCH + '\n' + THEME)
        # GRUB's native hidden countdown draws no menu on normal USB startup.
        # Keep a one-second key window: zero cannot reliably expose recovery
        # with Esc/F8/Shift before the default entry starts.
        text = text.replace('set default="1"', 'set default="0"').replace(
            'set timeout=60', 'set timeout_style=hidden\nset timeout=1')
        # Normal boots use Plymouth. The native rescue/basic-graphics entries
        # keep their original diagnostic behavior, and Esc can expose details.
        text = text.replace('@ROOT@ quiet', '@ROOT@ rhgb quiet')
        text = text.replace('rd.live.check quiet', 'rd.live.check rhgb quiet')
        changes[path.relative_to(source)] = text
    path = generic / 'x86.tmpl'
    text = path.read_text()
    if text.count(ARCH) != 1 or text.count('## make boot.iso') != 1:
        raise ValueError('unsupported Lorax x86 template layout')
    changes[path.relative_to(source)] = text.replace(ARCH, ARCH + ASSETS)
    shutil.copytree(source, destination)
    for relative, text in changes.items():
        (destination / relative).write_text(text)

if __name__ == '__main__':
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
