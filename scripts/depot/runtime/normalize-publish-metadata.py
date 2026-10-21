#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Make staged Flatpak metadata readable without modifying source inodes."""
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid


def normalize(runtime: Path) -> None:
    directory = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    temporary = '.metadata-' + uuid.uuid4().hex
    try:
        descriptor = os.open('metadata', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Runtime metadata must be a regular file')
        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
        with os.fdopen(os.dup(descriptor), 'rb') as source, os.fdopen(output, 'wb') as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fchmod(target.fileno(), 0o644)
            os.fsync(target.fileno())
        os.replace(temporary, 'metadata', src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


if __name__ == '__main__':
    normalize(Path(sys.argv[1]))
