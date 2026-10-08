#!/usr/bin/env python3
"""Run the required kit unit files in isolated processes and session buses."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--logs', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
files = sorted((root / 'tests/unit').glob('test_luma_appkit*.py'))
if not files:
    raise SystemExit('No required kit unit files found')
args.logs.mkdir(parents=True, exist_ok=True)
failed = []
for test in files:
    with (args.logs / (test.stem + '.log')).open('w') as log:
        result = subprocess.run([
            'dbus-run-session', '--config-file=' + str(Path(__file__).with_name('private-session.conf')),
            '--', sys.executable, '-W',
            'ignore::DeprecationWarning', '-m', 'unittest', 'discover',
            '-s', 'tests/unit', '-p', test.name,
        ], cwd=root, stdout=log, stderr=subprocess.STDOUT)
    output = (args.logs / (test.stem + '.log')).read_text()
    count = re.search(r'Ran (\d+) tests? in ', output)
    skipped = re.search(r'OK \(skipped=(\d+)\)', output)
    measured = count is not None and int(count[1]) > 0
    if skipped and count and int(skipped[1]) == int(count[1]):
        measured = False
    passed = result.returncode == 0 and measured
    print(f'{test.name}: {"PASS" if passed else "FAIL"} ({count[1] if count else 0} tests)', flush=True)
    if not passed:
        failed.append(test.name)
if failed:
    raise SystemExit('Failed: ' + ', '.join(failed))
print(f'All {len(files)} required kit unit files passed')
