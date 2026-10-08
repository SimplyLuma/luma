#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Prints the frame-time table for blur-motion runs and fails when a Frost or
# Glass window drag costs more than a set margin over Light.
# Usage: compare.py OUT_ROOT [--margin 1.25] [--slack-ms 1.0]
#   OUT_ROOT holds out-<theme>-cache<0|1>/blur-motion.json directories.
import argparse
import json
import pathlib
import sys

p = argparse.ArgumentParser()
p.add_argument('root')
p.add_argument('--margin', type=float, default=1.25)
p.add_argument('--slack-ms', type=float, default=1.0)
args = p.parse_args()

runs = {}
for f in sorted(pathlib.Path(args.root).glob('out-*/blur-motion.json')):
    data = json.loads(f.read_text())
    runs[f.parent.name.removeprefix('out-')] = data

if not runs:
    sys.exit('no blur-motion.json runs found')

actions = sorted({a for r in runs.values() for a in r.get('actions', {})})
print(f"{'run':<18}{'action':<24}{'frames':>7}{'fps':>7}{'mean':>8}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}")
for name, r in runs.items():
    if r.get('error'):
        print(f'{name:<18}ERROR {r["error"]}')
    for a in actions:
        s = r.get('actions', {}).get(a)
        if s:
            print(f"{name:<18}{a:<24}{s['frames']:>7}{s['fps']:>7}{s['mean']:>8}{s['p50']:>8}{s['p95']:>8}{s.get('p99', '-'):>8}{s['max']:>8}")

failures = []
light = runs.get('light-cache1') or runs.get('light-cache0')
if not light:
    failures.append('no light run to compare against')
for theme in ('frost', 'glass'):
    run = runs.get(f'{theme}-cache1')
    if not run or not light:
        failures.append(f'missing {theme}-cache1 run')
        continue
    if run.get('error'):
        failures.append(f'{theme}: {run["error"]}')
        continue
    for metric in ('mean', 'p95'):
        base = light['actions']['drag-across-monitors'][metric]
        got = run['actions']['drag-across-monitors'][metric]
        limit = base * args.margin + args.slack_ms
        if got > limit:
            failures.append(f'{theme} drag {metric} {got} ms > {limit:.2f} ms '
                            f'(light {base} ms x {args.margin} + {args.slack_ms} ms)')

for f in failures:
    print('FAIL', f)
print('blur-motion:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
