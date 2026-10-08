#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Check native GSK decoration captures against the shared source tokens."""
import argparse
import json
from pathlib import Path
import re


def check(root, tokens):
    cases = 0
    for mode, profile in tokens['window_elevation'].items():
        expected = []
        for item in re.finditer(r'0 (\d+)px (\d+)px(?: (-?\d+)px)? rgba\((\d+),(\d+),(\d+),([.\d]+)\)', profile['shadow']):
            y, blur, spread, r, g, b, alpha = item.groups()
            expected.append((float(y), float(blur), float(spread or 0), (int(r), int(g), int(b), float(alpha))))
        assert len(expected) == 3, mode
        focused = None
        for state in ('focused', 'backdrop', 'disabled', 'maximized', 'tiled', 'fullscreen'):
            text = (root / f'{mode}-{state}.node').read_text()
            outer = re.findall(r'outset-shadow\s*\{([^}]+)\}', text)
            if state in ('focused', 'backdrop'):
                assert len(outer) == 3, (mode, state, len(outer))
                for block, (y, blur, spread, color) in zip(outer, expected):
                    def number(name):
                        value = re.search(r'\b'+name+r': ([\d.-]+);', block)
                        return float(value.group(1)) if value else 0
                    assert (number('dx'), number('dy'), number('blur'), number('spread')) == (0, y, blur, spread), (mode, state, block)
                    rgba = re.search(r'color: rgba\((\d+),(\d+),(\d+),([.\d]+)\)', block)
                    assert rgba and tuple(map(float, rgba.groups())) == color, (mode, state, block)
                if focused is None:
                    focused = outer
                else:
                    assert focused == outer, f'{mode}: focus changed a shadow'
                assert text.count('inset-shadow {') == 1, (mode, state)
            elif state == 'disabled':
                assert not outer, (mode, state)
                assert text.count('inset-shadow {') == 1, f'{mode}: disabled shadow lost edge stroke'
                alpha = float(re.search(r',([.\d]+)\)', profile['stroke']).group(1))
                assert f'rgba(255,255,255,{alpha:g})' in text, (mode, state)
            else:
                assert not outer and 'inset-shadow {' not in text, (mode, state)
            cases += 1
    return cases


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('captures', type=Path)
    parser.add_argument('--tokens', type=Path, default=Path(__file__).resolve().parents[2] / 'config/shared/design-tokens.json')
    args = parser.parse_args()
    print(json.dumps({'passed': check(args.captures, json.loads(args.tokens.read_text())), 'renderer': 'native GSK', 'capture_path': str(args.captures)}))
