# SPDX-License-Identifier: MPL-2.0
"""On-demand local space analysis; metadata only, no deletion or elevation."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time

from .backend import DiskError


@dataclass(frozen=True)
class UsageEntry:
    path: str
    name: str
    directory: bool
    allocated: int
    apparent: int
    files: int
    skipped: int


@dataclass(frozen=True)
class UsageReport:
    path: str
    entries: tuple[UsageEntry, ...]
    allocated: int
    apparent: int
    files: int
    skipped: int
    complete: bool
    cancelled: bool
    limited: bool


def scan_folder(folder, cancel, progress=lambda count: None, *, max_entries=250_000):
    """Aggregate immediate children without retaining an entire filesystem tree.

    Symlinks are never followed, other filesystems are not traversed, and hard
    links count once per scan. Reflinks, compression and inaccessible folders
    mean allocated totals need not equal filesystem-wide used space.
    """
    root = Path(folder).resolve(strict=True)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise DiskError('Choose a folder to analyze.')
    device = info.st_dev
    groups = {}
    hardlinks = set()
    skipped = files = visited = 0
    last_progress = time.monotonic()
    stack = [(root, None, (info.st_dev,info.st_ino))]
    directories=set()
    limited = False
    while stack and not cancel.is_set():
        directory, owner, expected = stack.pop()
        fd=None
        try:
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            opened=os.fstat(fd)
            identity=(opened.st_dev,opened.st_ino)
            if identity!=expected or opened.st_dev!=device or identity in directories:
                raise OSError('Directory changed or was already scanned')
            directories.add(identity)
            with os.scandir(fd) as items:
                for item in items:
                    if cancel.is_set():
                        break
                    visited += 1
                    if visited > max_entries:
                        limited = True
                        break
                    try:
                        info = item.stat(follow_symlinks=False)
                    except OSError:
                        skipped += 1
                        if owner is not None:
                            groups[owner]['skipped'] += 1
                        continue
                    if stat.S_ISLNK(info.st_mode) or info.st_dev != device:
                        skipped += 1
                        if owner is not None:
                            groups[owner]['skipped'] += 1
                        continue
                    is_dir = stat.S_ISDIR(info.st_mode)
                    if not is_dir and not stat.S_ISREG(info.st_mode):
                        skipped += 1
                        continue
                    item_path=str(directory/item.name)
                    key = owner if owner is not None else item_path
                    if key not in groups:
                        groups[key] = dict(path=item_path, name=item.name, directory=is_dir,
                                           allocated=0, apparent=0, files=0, skipped=0)
                    group = groups[key]
                    if is_dir:
                        group['allocated'] += info.st_blocks * 512
                        stack.append((Path(item_path), key, (info.st_dev,info.st_ino)))
                    else:
                        identity = (info.st_dev, info.st_ino)
                        if info.st_nlink > 1:
                            if identity in hardlinks:
                                continue
                            hardlinks.add(identity)
                        group['allocated'] += info.st_blocks * 512
                        group['apparent'] += info.st_size
                        group['files'] += 1
                        files += 1
                    now = time.monotonic()
                    if now - last_progress >= .2:
                        progress(visited)
                        last_progress = now
                if limited:
                    break
        except OSError:
            skipped += 1
            if owner is not None:
                groups[owner]['skipped'] += 1
        finally:
            if fd is not None: os.close(fd)
    entries = tuple(sorted((UsageEntry(**value) for value in groups.values()),
                           key=lambda entry: (-entry.allocated, entry.name.casefold())))
    cancelled = cancel.is_set()
    return UsageReport(str(root), entries, sum(e.allocated for e in entries),
                       sum(e.apparent for e in entries), files, skipped,
                       not (skipped or cancelled or limited), cancelled, limited)
