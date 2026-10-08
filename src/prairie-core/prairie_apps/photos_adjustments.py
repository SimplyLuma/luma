# SPDX-License-Identifier: Apache-2.0
"""Reversible photo settings in the catalog's existing metadata table.

No image file is written. Reads use SQLite mode=ro; saves use mode=rw (never
create a catalog), back it up before the first adjustment write, and merge
only the edited fields into the latest settings in one transaction.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile

SLIDERS = ('e', 'c', 'hi', 'sh', 'w', 'sat')
PRESETS = ('', 'vivid', 'warm', 'cool', 'mono', 'fade')
AUTO = {'e': 10, 'c': 16, 'hi': -18, 'sh': 22, 'sat': 10, 'w': 4}
KEYS = (*SLIDERS, 'pre', 'rot', 'flip', 'crop', 'crop_rect')
CROPS = ('original', 'square', '4:3', '16:9')


def has_edits(values: Mapping[str, object]) -> bool:
    """Whether a saved setting changes pixels, including unknown future edits.

    Neutral settings left by a reverted edit should still export exact original
    bytes. Unknown settings are never silently treated as an unedited photo.
    """
    neutral = {**dict.fromkeys(SLIDERS, 0), 'pre': '', 'rot': 0,
               'flip': False, 'crop': 'original', 'crop_rect': [0, 0, 1, 1]}
    return any(key not in neutral or
               (list(value) if key == 'crop_rect' and isinstance(value, (list, tuple)) else value) != neutral[key]
               for key, value in values.items())


def apply_changes(current: Mapping[str, object], changes: Mapping[str, object]) -> dict[str, object]:
    """Merge edited settings, keeping untouched and future fields intact."""
    values = dict(current)
    for key, value in changes.items():
        if key not in KEYS:
            raise ValueError(f'Unknown photo adjustment: {key}')
        if value is None:
            values.pop(key, None)
            continue
        if key in SLIDERS:
            valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value) and -100 <= value <= 100)
        elif key == 'rot':
            valid = isinstance(value, int) and not isinstance(value, bool) and value % 90 == 0
        elif key == 'flip':
            valid = isinstance(value, bool)
        elif key == 'crop':
            valid = isinstance(value, str) and value in CROPS
        elif key == 'crop_rect':
            valid = (isinstance(value, (tuple, list)) and len(value) == 4 and
                     all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value))
            if valid:
                x, y, w, h = value
                valid = min(x, y) >= 0 and min(w, h) >= .001 and x+w <= 1.00000001 and y+h <= 1.00000001
        else:
            valid = isinstance(value, str) and value in PRESETS
        if not valid:
            raise ValueError(f'Invalid {key} adjustment: {value!r}')
        values[key] = list(value) if key == 'crop_rect' else value
    return values


def changed_settings(before: Mapping[str, object], after: Mapping[str, object]) -> dict[str, object]:
    """A changes-only save, including fields explicitly reverted to original."""
    return {key: after.get(key) for key in KEYS
            if before.get(key) != after.get(key) or (key in before) != (key in after)}


class AdjustmentStore:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.backup_path = self.database.with_name('library-before-photo-adjustments.sqlite3')

    @staticmethod
    def key(asset_id: str) -> str:
        return f'photos.adjustments.{asset_id}'

    def _connect(self, mode: str) -> sqlite3.Connection:
        return sqlite3.connect(self.database.resolve().as_uri() + f'?mode={mode}',
                               uri=True, timeout=20)

    @staticmethod
    def _read(connection: sqlite3.Connection, key: str) -> dict[str, object]:
        row = connection.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        value = json.loads(row[0]) if row else {}
        if not isinstance(value, dict):
            raise ValueError('Invalid saved photo adjustments')
        return value

    def load(self, asset_id: str) -> dict[str, object]:
        if not self.database.is_file():
            return {}
        connection = self._connect('ro')
        try:
            return self._read(connection, self.key(asset_id))
        finally:
            connection.close()

    def _backup(self) -> None:
        if self.backup_path.exists() or self.backup_path.is_symlink():
            if not self.backup_path.is_file() or self.backup_path.is_symlink():
                raise OSError('Catalog backup path is not a regular file')
            # Refuse a truncated or invalid backup, even if its name exists.
            backup = sqlite3.connect(self.backup_path.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise OSError('Catalog backup is not valid')
                if not backup.execute("SELECT 1 FROM sqlite_master WHERE name='assets'").fetchone():
                    raise OSError('Catalog backup has no photos catalog')
            finally:
                backup.close()
            return
        descriptor, name = tempfile.mkstemp(prefix='.photos-adjustment-backup-', dir=self.database.parent)
        os.close(descriptor)
        temporary = Path(name)
        source = target = None
        try:
            source = self._connect('ro')
            target = sqlite3.connect(temporary)
            source.backup(target)
            target.close()
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            try:
                # Link atomically without replacing the first backup if another
                # window completed one while we were taking our snapshot.
                os.link(temporary, self.backup_path)
            except FileExistsError:
                self._backup()
            directory = os.open(self.database.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
            temporary.unlink(missing_ok=True)

    def update(self, asset_id: str, changes: Mapping[str, object]) -> dict[str, object]:
        apply_changes({}, changes)  # reject invalid input before any I/O
        if not changes:
            return self.load(asset_id)
        connection = self._connect('rw')
        try:
            if not connection.execute('SELECT 1 FROM assets WHERE id=?', (asset_id,)).fetchone():
                raise KeyError(asset_id)
            self._backup()  # a failure prevents every new adjustment write
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT 1 FROM assets WHERE id=?', (asset_id,)).fetchone():
                raise KeyError(asset_id)
            key = self.key(asset_id)
            values = apply_changes(self._read(connection, key), changes)
            if values:
                connection.execute('INSERT INTO metadata(key,value) VALUES (?,?) '
                                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                   (key, json.dumps(values, allow_nan=False, sort_keys=True)))
            else:
                connection.execute('DELETE FROM metadata WHERE key=?', (key,))
            connection.commit()
            return values
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
