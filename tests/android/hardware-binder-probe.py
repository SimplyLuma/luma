#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only host Binder admission check in a disposable Android test VM.

The fixed unknown transaction cannot invoke hardware. Run the trusted control
and then an ordinary Android UID: absence or a generic IPC failure never pass.
Android's own service client supplies its image's exact Binder wire protocol.
"""
import argparse
import json
import os
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', choices=('hardware', 'clipboard', 'usermonitor'), default='hardware')
    parser.add_argument('--uid', required=True, type=int)
    parser.add_argument('--expect', required=True, choices=('denied', 'control'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as the disposable VM administrator')
    trusted = (0, 1000) if args.service == 'hardware' else (1000,)
    if not (args.uid in trusted and args.expect == 'control' or
            10000 <= args.uid < 100000 and args.expect == 'denied'):
        parser.error('Use a trusted service UID for control and an application UID for denial')
    result = subprocess.run([
        'lxc-attach', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid',
        '--uid=' + str(args.uid), '--gid=' + str(args.uid), '--clear-env', '--',
        '/system/bin/service', 'call', 'waydroid' + args.service, '65536',
    ], text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise SystemExit('Android Binder client failed: ' + result.stderr[:1024])
    word = re.search(r'Result: Parcel\(\s*0x00000000:\s*([0-9a-fA-F]{8})\b', result.stdout)
    if not word:
        raise SystemExit('Missing hardware admission reply; endpoint absence does not pass')
    value = int(word[1], 16)
    exception = value - 2**32 if value >= 2**31 else value
    verdict = {-1: 'denied', -7: 'control'}.get(exception, 'unexpected')
    print(json.dumps({'service': args.service, 'android_uid': args.uid, 'transaction': 'unknown/read-only',
                      'exception': exception, 'verdict': verdict}))
    if verdict != args.expect:
        raise SystemExit('Unexpected hardware Binder admission result')


if __name__ == '__main__':
    main()
