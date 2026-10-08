# SPDX-License-Identifier: Apache-2.0
"""Contained Ari writes: preserve unrelated fields and one original backup.

These helpers run only in the daemon's existing write paths, never in a fixture
or while the UI reads real data. Backups stay beside Ari's own config/database.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import tempfile


@contextmanager
def _locked(path):
    descriptor = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def backup_path(path):
    return Path(str(path) + '.pre-lumaui.bak')


def _atomic_bytes(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_json(path, updates, *, removed=(), defaults=None):
    """Under one lock, back up and change only explicitly named fields.

    Invalid existing JSON is an error: it must never become a blank config.
    Defaults apply only to absent fields (a completed model download).
    """
    path = Path(path)
    with _locked(path):
        original = path.read_bytes() if path.exists() else None
        current = json.loads(original) if original is not None else {}
        if not isinstance(current, dict):
            raise ValueError('Ari configuration must be an object')
        if original is not None and not backup_path(path).exists():
            _atomic_bytes(backup_path(path), original)
        current.update(updates)
        for key in removed:
            current.pop(key, None)
        for key, value in (defaults or {}).items():
            current.setdefault(key, value)
        _atomic_bytes(path, (json.dumps(current, indent=1) + '\n').encode())
        return current


def backup_database_once(connection, path):
    """SQLite's online backup includes committed WAL data; never copy raw DB.

    Called before schema initialization can write an already existing database.
    An atomic rename prevents a failed backup being mistaken for a complete one.
    """
    path = Path(path)
    with _locked(path):
        backup = backup_path(path)
        if backup.exists():
            return
        descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        os.close(descriptor)
        try:
            target = sqlite3.connect(temporary)
            try:
                connection.backup(target)
            finally:
                target.close()
            os.replace(temporary, backup)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
