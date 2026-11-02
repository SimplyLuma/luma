#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Merge the Luma Raspberry Pi kernel configuration, then audit the result.

merge: start from Fedora's configuration for the same kernel series, add every
option Raspberry Pi's defconfig enables (y beats m; Fedora never loses an
option to the Pi side), then apply the Luma fragment, which has the last word.

check: after `make olddefconfig`, list every Fedora or Raspberry Pi option the
final configuration lost. Each loss must be named in the exceptions file with
a reason, or the check fails. This keeps "Fedora userspace works" and "every
Raspberry Pi board feature is there" true across kernel bumps.
"""
import argparse
import pathlib
import re
import sys

LINE = re.compile(r'^(CONFIG_[A-Za-z0-9_]+)=(.*)$')
UNSET = re.compile(r'^# (CONFIG_[A-Za-z0-9_]+) is not set$')


def parse(path):
    values = {}
    for line in pathlib.Path(path).read_text().splitlines():
        if m := LINE.match(line):
            values[m[1]] = m[2]
        elif m := UNSET.match(line):
            values[m[1]] = 'n'
    return values


def write(values, path):
    with open(path, 'w') as out:
        for sym, val in values.items():
            out.write(f'# {sym} is not set\n' if val == 'n' else f'{sym}={val}\n')


def exceptions(path):
    rows = {}
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        sym, _, reason = line.partition(' ')
        if not reason.strip():
            raise SystemExit(f'{path}: {sym} has no reason')
        rows[sym] = reason.strip()
    return rows


def merge(args):
    fedora, pi, luma = parse(args.fedora), parse(args.pi), parse(args.fragment)
    result = dict(fedora)
    for sym, val in pi.items():
        have = fedora.get(sym)
        if val in ('y', 'm'):
            if have in (None, 'n') or (have == 'm' and val == 'y'):
                result[sym] = val
        elif have is None:
            # Strings and numbers only for options Fedora's tree lacks
            # (Raspberry Pi drivers); Fedora's values stand otherwise.
            result[sym] = val
    result.update(luma)
    write(result, args.output)


def check(args):
    fedora, pi, luma = parse(args.fedora), parse(args.pi), parse(args.fragment)
    final = parse(args.final)
    allowed = exceptions(args.exceptions)
    lost, unused = [], set(allowed)
    for origin, values in (('fedora', fedora), ('raspberrypi', pi)):
        for sym, val in values.items():
            if val not in ('y', 'm') or sym in luma:
                continue
            got = final.get(sym, 'n')
            if got in ('y', 'm'):
                continue
            unused.discard(sym)
            if sym not in allowed:
                lost.append(f'{origin}: {sym}={val} -> {got}')
    bad_luma = [f'luma: {s}={v} -> {final.get(s, "n")}' for s, v in luma.items()
                if final.get(s, 'n') != v]
    if args.report:
        with open(args.report, 'w') as out:
            for sym in sorted(set(allowed) - unused):
                out.write(f'dropped {sym}: {allowed[sym]}\n')
    for line in lost + bad_luma:
        print(line, file=sys.stderr)
    for sym in sorted(unused):
        print(f'note: exception {sym} is not needed any more', file=sys.stderr)
    if lost or bad_luma:
        raise SystemExit(f'{len(lost)} options lost, {len(bad_luma)} Luma settings not applied')
    print(f'config check: fedora {sum(v in "ym" for v in fedora.values())}, '
          f'raspberrypi {sum(v in "ym" for v in pi.values())} options kept; '
          f'{len(allowed) - len(unused)} documented exceptions')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name in ('merge', 'check'):
        p = sub.add_parser(name)
        p.add_argument('--fedora', required=True)
        p.add_argument('--pi', required=True, help='expanded Raspberry Pi defconfig')
        p.add_argument('--fragment', required=True)
    sub.choices['merge'].add_argument('--output', required=True)
    sub.choices['check'].add_argument('--final', required=True)
    sub.choices['check'].add_argument('--exceptions', required=True)
    sub.choices['check'].add_argument('--report')
    args = parser.parse_args()
    merge(args) if args.cmd == 'merge' else check(args)


if __name__ == '__main__':
    main()
