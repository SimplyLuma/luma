#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Adapt the installed, pinned Lorax templates at their native build boundary."""
import shutil
import subprocess
import sys
from pathlib import Path

THEME = '''
# Project Luma: the same source-owned fonts and theme as the installed OS.
insmod gfxterm
insmod gfxmenu
loadfont /boot/grub2/themes/luma/prairie-12.pf2
loadfont /boot/grub2/themes/luma/prairie-22.pf2
if loadfont /boot/grub2/themes/luma/prairie.pf2; then
  if [ "${timeout_style}" != "hidden" ]; then
    terminal_output gfxterm
  fi
fi
set theme=/boot/grub2/themes/luma/theme.txt
'''
SEARCH = "search --no-floppy --set=root -l '@ISOLABEL@'"
ARCH = '## configure grub2 config file\nmkdir ${GRUB2DIR}\n'
ASSETS = '''mkdir ${GRUB2DIR}/themes/luma
install usr/share/luma/boot/grub-theme/* ${GRUB2DIR}/themes/luma/
'''

def verify_iso(root, architecture):
    if architecture not in ('x86_64', 'aarch64'):
        raise ValueError(f'unsupported installer architecture: {architecture}')
    configs = [root / 'efi-grub.cfg']
    if architecture == 'x86_64':
        configs.append(root / 'grub2/grub.cfg')
    for path in configs:
        text = path.read_text()
        if 'set theme=/boot/grub2/themes/luma/theme.txt' not in text:
            raise ValueError(f'{path}: installer GRUB theme missing')
        if '@ROOT@' in text:
            raise ValueError(f'{path}: unresolved GRUB boot root')
        grammar = subprocess.run(['grub2-script-check', str(path)],
                                 capture_output=True, text=True)
        diagnostics = grammar.stdout + grammar.stderr
        # GRUB2.12 can return0 even when its lexer prints a syntax error.
        if grammar.returncode or 'error:' in diagnostics.lower() or 'syntax error' in diagnostics.lower():
            raise ValueError(f'{path}: invalid GRUB script: {diagnostics.strip()}')

def prepare(source, destination, architecture='x86_64'):
    if architecture not in ('x86_64', 'aarch64'):
        raise ValueError(f'unsupported installer architecture: {architecture}')
    generic = source / 'templates.d/99-generic'
    changes = {}
    directory = 'x86' if architecture == 'x86_64' else 'aarch64'
    for kind in (('bios', 'efi') if architecture == 'x86_64' else ('efi',)):
        path = generic / f'config_files/{directory}/grub2-{kind}.cfg'
        text = path.read_text()
        for marker in (SEARCH, 'set default="1"', 'set timeout=60'):
            if text.count(marker) != 1:
                raise ValueError(f'{path}: unsupported Lorax boot layout: {marker}')
        recovery = (('inst.rescue quiet', 'nomodeset quiet') if architecture == 'x86_64'
                    else ('@ROOT@ inst.rescue\n', '@ROOT@ nomodeset\n'))
        if any(marker not in text for marker in recovery):
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
        if architecture == 'aarch64':
            # Fedora's ARM menu has no quiet flags. Preserve its diagnostic
            # entries and enable the same normal Plymouth handoff as x86.
            text = text.replace('@ROOT@ ro', '@ROOT@ ro rhgb quiet').replace(
                '@ROOT@ rd.live.check\n', '@ROOT@ rd.live.check rhgb quiet\n')
        changes[path.relative_to(source)] = text
    path = generic / f'{directory}.tmpl'
    text = path.read_text()
    if text.count('## make boot.iso') != 1:
        raise ValueError('unsupported Lorax boot template layout')
    if architecture == 'x86_64':
        if text.count(ARCH) != 1:
            raise ValueError('unsupported Lorax x86 template layout')
        text = text.replace(ARCH, ARCH + ASSETS)
    else:
        # ARM's native template builds only UEFI media and grafts selected
        # directories, unlike x86's whole-output-tree graft. Carry the theme
        # explicitly, retaining Fedora's shim/GRUB AA64 boot chain.
        if text.count("efiarch64 = 'AA64'") != 1 or '${efigraft} ${filegraft}' not in text:
            raise ValueError('unsupported Lorax aarch64 template layout')
        text = text.replace('## make boot.iso', '''mkdir boot/grub2/themes/luma
install usr/share/luma/boot/grub-theme/* boot/grub2/themes/luma/
<% filegraft += " boot/grub2={0}/boot/grub2".format(outroot) %>
## make boot.iso''')
    changes[path.relative_to(source)] = text
    # Userspace FAT tools avoid a host loop mount on either architecture while
    # retaining the exact upstream shim/GRUB files and complete readback.
    path = generic / 'efi.tmpl'
    text = path.read_text()
    command = 'runcmd mkefiboot ${args} ${outroot}/${EFIBOOTDIR} ${outroot}/${img}'
    if text.count(command) != 1:
        raise ValueError('unsupported Lorax EFI image creation layout')
    changes[path.relative_to(source)] = text.replace(
        command, 'runcmd python3 /s/make-efi-image.py ${args} ${outroot}/${EFIBOOTDIR} ${outroot}/${img}')
    shutil.copytree(source, destination)
    for relative, text in changes.items():
        (destination / relative).write_text(text)

if __name__ == '__main__':
    if sys.argv[1] == '--verify-iso':
        verify_iso(Path(sys.argv[2]), sys.argv[3])
    else:
        prepare(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else 'x86_64')
