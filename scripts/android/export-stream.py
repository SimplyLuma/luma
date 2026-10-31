#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Copy a bounded build-artifact stream with checksums and incremental sync.

The caller owns the destination directory and producer process. Stream waits
have a deadline; kernel filesystem writes/syncs depend on the underlying device.
Unverified partial files remain available as evidence and are never published.
"""
import hashlib
import os
from pathlib import Path
import re
import select
import time


def copy_verified_stream(source, destination, *, size, digest, deadline,
                         sync_bytes=8 * 1024**2):
    destination = Path(destination)
    if not isinstance(size, int) or not 0 <= size <= 8 * 1024**3:
        raise ValueError('Invalid artifact size')
    if not re.fullmatch('[0-9a-f]{64}', digest) or sync_bytes <= 0:
        raise ValueError('Invalid checksum or sync budget')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    partial = destination.with_name(destination.name + '.partial')
    received = unsynced = 0
    checksum = hashlib.sha256()
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Artifact stream deadline')
            readable, _, _ = select.select([source], [], [], min(remaining, 5))
            if not readable:
                continue
            chunk = os.read(source.fileno(), 65536)
            if not chunk:
                break
            if received + len(chunk) > size:
                raise ValueError('Artifact exceeds declared size')
            checksum.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError('Artifact write made no progress')
                view = view[written:]
            received += len(chunk)
            unsynced += len(chunk)
            if unsynced >= sync_bytes:
                os.fdatasync(descriptor)
                unsynced = 0
        if received != size or checksum.hexdigest() != digest:
            raise ValueError('Artifact size or checksum mismatch')
        os.fdatasync(descriptor)
        os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(descriptor)
    # Same-directory hard-link publication fails if a destination appeared;
    # neither an existing artifact nor a symlink can be overwritten.
    os.link(partial, destination, follow_symlinks=False)
    partial.unlink()
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {'bytes': received, 'sha256': checksum.hexdigest()}
