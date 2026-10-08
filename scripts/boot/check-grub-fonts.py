#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check GRUB's actual PF2 names, not the filenames, against its theme."""
import re
import struct
import sys
from pathlib import Path

def name(path):
    data = path.read_bytes()
    offset = 0
    while offset + 8 <= len(data):
        tag, length = data[offset:offset + 4], struct.unpack('>I', data[offset + 4:offset + 8])[0]
        offset += 8
        if length == 0xffffffff:
            break
        payload = data[offset:offset + length]
        if len(payload) != length:
            raise ValueError(f'{path}: truncated PF2')
        if tag == b'NAME':
            return payload.rstrip(b'\0').decode('utf-8')
        offset += length
    raise ValueError(f'{path}: missing PF2 NAME')

def check(directory):
    theme = (directory / 'theme.txt').read_text()
    # GRUB interprets even an empty terminal-box as a pixmap pattern and
    # interrupts startup with a missing '*' error. Omit the optional box when
    # there are no nine-slice images instead of requesting an invalid one.
    for pattern in re.findall(r'\bterminal-box\s*:\s*"([^"]*)"', theme):
        if '*' not in pattern:
            raise ValueError(f'GRUB terminal-box requires a pixmap pattern: {pattern!r}')
    fonts = {name(path) for path in directory.glob('*.pf2')}
    requests = set(re.findall(r'(?:\bfont|[-_]font)\s*[:=]\s*"([^"]+)"', theme))
    if not requests:
        raise ValueError('theme requests no fonts')
    missing = requests - fonts
    if missing:
        raise ValueError(f'GRUB requests unavailable font names: {sorted(missing)}; packaged: {sorted(fonts)}')
    return fonts

if __name__ == '__main__':
    print('GRUB fonts:', ', '.join(sorted(check(Path(sys.argv[1])))))
