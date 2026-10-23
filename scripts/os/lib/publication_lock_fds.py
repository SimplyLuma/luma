#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate inherited OS publication locks without reopening their files."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import sys


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate publication lock key')
        result[key] = value
    return result


def validate(value, root, channels):
    if not channels or len(set(channels)) != len(channels) or set(channels) - {'nightly', 'beta', 'stable'}:
        raise ValueError('unsupported configured publication channel')
    mapping = json.loads(value, object_pairs_hook=unique_object)
    if not isinstance(mapping, dict):
        raise ValueError('publication lock map must be a JSON object')
    allowed = set(channels) | {'repository'}
    if set(mapping) - allowed:
        raise ValueError('unknown publication lock key')
    if any(type(fd) is not int or fd < 3 for fd in mapping.values()):
        raise ValueError('publication lock descriptors must be integers >= 3')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('publication lock descriptors must be distinct')
    # Validate every member before acquiring any. Inherited locks are the same
    # open file descriptions the outer operator holds, not a second open().
    for channel, fd in mapping.items():
        path = Path(root)/'locks'/('publish-'+channel+'.lock')
        expected, actual = path.stat(), os.fstat(fd)
        if not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode):
            raise ValueError('publication locks require regular files')
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError('publication lock descriptor names another inode')
    for fd in mapping.values():
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return mapping


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--channels', nargs='+', required=True)
    args = parser.parse_args()
    try:
        result = validate(os.environ['LUMA_OS_PUBLICATION_LOCK_FDS'], args.root, args.channels)
    except (ValueError, KeyError, OSError) as error:
        sys.exit('invalid inherited publication locks: '+str(error))
    for channel, fd in result.items():
        print(channel, fd)


if __name__ == '__main__': main()
