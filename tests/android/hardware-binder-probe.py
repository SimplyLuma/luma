#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only hardware Binder admission check in a disposable Android test VM.

The fixed unknown transaction cannot invoke hardware. Run the root control
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
    parser.add_argument('--uid', required=True, type=int)
    parser.add_argument('--expect', required=True, choices=('denied', 'control'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as the disposable VM administrator')
    if not (args.uid == 0 and args.expect == 'control' or
            10000 <= args.uid < 100000 and args.expect == 'denied'):
        parser.error('Use root for the control and a user-zero application UID for denial')
    result = subprocess.run([
        'lxc-attach', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid',
        '--uid=' + str(args.uid), '--gid=' + str(args.uid), '--clear-env', '--',
        '/system/bin/service', 'call', 'waydroidhardware', '65536',
    ], check=True, text=True, capture_output=True, timeout=15)
    word = re.search(r'Result: Parcel\(\s*0x00000000:\s*([0-9a-fA-F]{8})\b', result.stdout)
    if not word:
        raise SystemExit('Missing hardware admission reply; endpoint absence does not pass')
    value = int(word[1], 16)
    exception = value - 2**32 if value >= 2**31 else value
    verdict = {-1: 'denied', -7: 'control'}.get(exception, 'unexpected')
    print(json.dumps({'android_uid': args.uid, 'transaction': 'unknown/read-only',
                      'exception': exception, 'verdict': verdict}))
    if verdict != args.expect:
        raise SystemExit('Unexpected hardware Binder admission result')


if __name__ == '__main__':
    main()
