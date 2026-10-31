#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reject build-workspace permissions in RPM directory metadata.

Input: rpm -qp --qf '[%{FILEMODES:octal} %{FILENAMES}\\n]' PACKAGE.rpm
Only package metadata is read; paths are never opened or modified.
"""
from pathlib import Path
import stat
import sys


def check(lines):
    for line in lines:
        mode, name = line.rstrip('\n').split(' ', 1)
        value = int(mode, 8)
        if stat.S_ISDIR(value) and stat.S_IMODE(value) != 0o755:
            raise ValueError(f'Unexpected RPM directory permissions: {mode} {name}')


if __name__ == '__main__':
    try:
        with Path(sys.argv[1]).open() as source:
            check(source)
    except (OSError, ValueError, IndexError) as error:
        raise SystemExit(str(error))
