#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Reject mixed runtime kernel cohorts before Lorax builds their initramfs."""
import sys
from pathlib import Path


def check(root, expected):
    boot = sorted(path.name.removeprefix('vmlinuz-') for path in (root / 'boot').glob('vmlinuz-*')
                  if not path.name.startswith('vmlinuz-0-rescue-'))
    modules = sorted(path.name for path in (root / 'usr/lib/modules').iterdir() if path.is_dir())
    if boot != [expected] or modules != [expected]:
        raise ValueError(f'installer runtime kernel cohort mismatch: expected {expected}; '
                         f'boot={boot}; modules={modules}')
    print(f'Installer runtime has one pinned kernel cohort: {expected}')


if __name__ == '__main__':
    check(Path(sys.argv[1]), sys.argv[2])
