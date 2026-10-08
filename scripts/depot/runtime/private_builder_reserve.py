# SPDX-License-Identifier: Apache-2.0
"""Absolute reserve for a newly generated private unsigned builder repository."""
import os
from pathlib import Path
import subprocess

MINIMUM = 10 * 1024 ** 3


def check_space(path):
    value = os.statvfs(path)
    available = value.f_bavail * value.f_frsize
    if available < MINIMUM:
        raise RuntimeError('Private runtime builder lacks its 10 GiB physical reserve')
    print('Private runtime builder physical bytes available:', available, flush=True)
    return available


def configure(repo: Path, requested: int):
    if requested != MINIMUM:
        raise ValueError('Private builder reserve must be exactly 10 GiB')
    if repo.exists() or repo.is_symlink():
        raise ValueError('Private reserve applies only to a new unsigned builder repository')
    check_space(repo.parent)
    subprocess.run(['ostree', '--repo=' + str(repo), 'init', '--mode=archive'], check=True)
    subprocess.run(['ostree', '--repo=' + str(repo), 'config', 'set',
                    'core.min-free-space-percent', '0'], check=True)
    # OSTree accepts whole MB/GB/TB values. Round up to preserve at least 10 GiB.
    subprocess.run(['ostree', '--repo=' + str(repo), 'config', 'set',
                    'core.min-free-space-size', '11GB'], check=True)
    verify(repo)


def verify(repo: Path):
    for key, expected in [('core.min-free-space-percent', '0'),
                          ('core.min-free-space-size', '11GB')]:
        actual = subprocess.check_output(['ostree', '--repo=' + str(repo),
                                          'config', 'get', key], text=True).strip()
        if actual != expected:
            raise RuntimeError('Private runtime repository reserve changed: ' + key)
    check_space(repo)
