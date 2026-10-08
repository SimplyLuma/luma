# SPDX-License-Identifier: Apache-2.0
"""Local file attachments: additive, private, content-addressed copies.

Files live in Notes' own files/ directory. The existing picture sync transport
does not upload them. Original files are read only and never moved or edited.
"""
import hashlib
import os
from pathlib import Path
import re
import tempfile

MAX_FILE_BYTES = 20 * 1024 * 1024
FILE_NAME = re.compile(r'^[0-9a-f]{64}\.[a-z0-9]{1,15}$')


def files_directory():
    from .notes_backend import notes_data_directory
    return notes_data_directory() / 'files'


def file_path(reference, directory=None):
    if not isinstance(reference, str) or not FILE_NAME.fullmatch(reference):
        raise ValueError('Invalid attachment reference')
    return (directory or files_directory()) / reference


def validate_file(run):
    file_path(run.get('src'))
    name, size = run.get('name'), run.get('size')
    if (not isinstance(name, str) or not name or '/' in name or '\\' in name
            or any(ord(c) < 32 for c in name)
            or not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_FILE_BYTES):
        raise ValueError('Invalid attachment name or size')


def store_file(source, directory=None):
    """Read a regular local file, keeping at most 20 MB, and atomically copy it."""
    source = Path(source)
    if not source.is_file():
        raise ValueError('Choose a regular file')
    with source.open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('This file is larger than 20 MB')
    suffix = source.suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,15}', suffix):
        suffix = '.blob'
    metadata = {'src': hashlib.sha256(data).hexdigest() + suffix,
                'name': source.name, 'size': len(data)}
    validate_file(metadata)
    folder = directory or files_directory()
    target = file_path(metadata['src'], folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.exists():
        with target.open('rb') as kept:
            existing = kept.read(MAX_FILE_BYTES + 1)
        if hashlib.sha256(existing).hexdigest() != metadata['src'].split('.', 1)[0]:
            raise OSError('The stored attachment does not match its reference')
        return metadata
    handle, temporary = tempfile.mkstemp(prefix='.file-', dir=folder)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_handle = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory_handle)
        finally:
            os.close(directory_handle)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return metadata
