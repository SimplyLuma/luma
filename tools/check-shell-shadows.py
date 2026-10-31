#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail on any multi-layer shadow in Shell stylesheets.

St paints one shadow per actor and parses one layer: extra layers are dropped
with "Ignoring excess values in shadow definition" and the last colour wins,
so a multi-layer rule never paints what it says. Usage:
check-shell-shadows.py FILE.css [FILE.css...]"""
import re
import sys


def layers(value):
    out, depth, current = [], 0, ''
    for ch in value:
        depth += ch == '('
        depth -= ch == ')'
        if ch == ',' and depth == 0:
            out.append(current.strip())
            current = ''
        else:
            current += ch
    out.append(current.strip())
    return out


def main(paths):
    bad = 0
    for path in paths:
        text = open(path, encoding='utf-8', errors='replace').read()
        for match in re.finditer(r'((?:box|text|icon)-shadow)\s*:\s*([^;{}]*);', text):
            value = match.group(2).strip()
            if value.startswith('if(') or len(layers(value)) < 2:
                continue
            line = text.count('\n', 0, match.start()) + 1
            print(f'{path}:{line}: {match.group(1)} has {len(layers(value))} layers; St paints one')
            bad += 1
    if bad:
        return 1
    print(f'single-layer shadows: {len(paths)} stylesheets')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
