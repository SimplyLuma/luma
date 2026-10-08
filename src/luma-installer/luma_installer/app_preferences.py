# SPDX-License-Identifier: Apache-2.0
"""Fixed per-application preference handoff; never a whole dconf export.

The host uses private relocatable reader schemas at approved native paths.
An app applies the typed snapshot only to absent user values in its own real
schema. Native settings and later sandbox edits are retained across retries.
"""
from __future__ import annotations
import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
from xml.sax.saxutils import escape

MAX_BYTES = 262144
SCHEMA = 'org.projectluma.app-preferences/v1'
KEYS = {
    'org.projectluma.Write': {
        'recent-files': 'as', 'pinned-files': 'as', 'autosave-minutes': 'i',
        'default-paste-mode': 's', 'show-navigator': 'b', 'show-inspector': 'b',
        'last-save-format': 's', 'zoom': 'd', 'page-theme': 's',
        'recent-fonts': 'as', 'show-advanced': 'b', 'show-welcome': 'b'},
    'org.projectluma.Grid': {'recent-files': 'as', 'show-welcome': 'b'},
    'org.projectluma.Stage': {'default-new-format': 's',
        'recovery-interval-seconds': 'u', 'present-without-motion': 'b',
        'show-welcome': 'b'},
    'org.projectluma.Session': {'recent-projects': 'as', 'default-input': 's',
        'default-output': 's', 'sample-rate': 'i', 'recording-format': 's',
        'buffer-frames': 'i', 'autosave-minutes': 'i', 'waveform-quality': 's',
        'show-welcome': 'b'},
    'org.projectluma.Reel': {'show-welcome': 'b'},
}
PATHS = {
    'org.projectluma.Write': ('/org/projectluma/write/',),
    # Grid deliberately retains its established settings path.
    'org.projectluma.Grid': ('/io/luma/grid/',),
    'org.projectluma.Stage': ('/org/projectluma/stage/', '/io/luma/stage/'),
    'org.projectluma.Session': ('/org/projectluma/session/',),
    'org.projectluma.Reel': ('/org/projectluma/Reel/',),
}

class PreferencesError(ValueError):
    pass

def reader_id(app):
    if app not in KEYS:
        raise PreferencesError('This application has no preference handoff contract.')
    return 'org.projectluma.AppPreferences.Read.' + app.rsplit('.', 1)[-1]

def reader_xml():
    defaults = {'as': '[]', 's': "''", 'b': 'false', 'i': '0', 'u': '0', 'd': '0.0'}
    rows = ['<?xml version="1.0" encoding="UTF-8"?>', '<schemalist>']
    for app, keys in KEYS.items():
        rows.append('<schema id="' + reader_id(app) + '">')
        for key, kind in keys.items():
            rows.append('<key name="' + key + '" type="' + kind + '"><default>' +
                        escape(defaults[kind]) + '</default></key>')
        rows.append('</schema>')
    return '\n'.join(rows + ['</schemalist>', ''])

def _gio():
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    return Gio, GLib

def _settings(app, *, reader=False, path=None, schema_source=None):
    Gio, _ = _gio()
    source = schema_source or Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(reader_id(app) if reader else app, True) if source else None
    if schema is None:
        raise PreferencesError('The installed preference schema is unavailable; existing data is preserved.')
    for key, kind in KEYS[app].items():
        if not schema.has_key(key) or schema.get_key(key).get_value_type().dup_string() != kind:
            raise PreferencesError('The installed preference schema needs review.')
    return Gio.Settings.new_full(schema, None, path), schema

def _directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022):
        raise PreferencesError('The preference directory has an unsafe owner or link.')

def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
            raise PreferencesError('The preference handoff file needs review.')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PreferencesError('The preference handoff is too large.')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise PreferencesError('The preference handoff contains duplicate fields.')
                result[key] = value
            return result
        value = json.loads(data, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise PreferencesError('The preference handoff is invalid.')
        return value, hashlib.sha256(data).hexdigest()
    finally:
        os.close(fd)

def _write(path, value, *, exclusive=False):
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode()
    if len(encoded) > MAX_BYTES:
        raise PreferencesError('The preference handoff is too large.')
    _directory(path.parent)
    if not exclusive:
        if path.exists() or path.is_symlink():
            _read(path)
    fd, temporary = tempfile.mkstemp(prefix='.preferences-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        if exclusive:
            # Publish a fully flushed inode without overwriting an existing
            # import. A stopped writer never leaves a partial import.json.
            os.link(temporary, path, follow_symlinks=False)
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)

def _bounded(app, key, variant):
    kind = KEYS[app][key]
    if variant.get_type_string() != kind or variant.get_size() > 65536:
        raise PreferencesError('A preference has an unsupported type or size.')
    value = variant.unpack()
    if kind == 'as' and (len(value) > 512 or any(len(s) > 4096 for s in value)):
        raise PreferencesError('The recent-item preference needs review.')
    if kind == 's' and len(value) > 16384:
        raise PreferencesError('A text preference is too large.')
    if kind == 'd' and not math.isfinite(value):
        raise PreferencesError('A numeric preference is invalid.')
    text = variant.print_(True)
    if len(text.encode()) > 131072:
        raise PreferencesError('A preference is too large.')
    return {'type': kind, 'text': text}

def capture(app_id, destination, *, schema_source=None):
    """Write import.json in a host-owned, already allocated recovery directory."""
    reader_id(app_id)
    destination = Path(destination)
    _directory(destination)
    values = {}
    for native_path in PATHS[app_id]:
        settings, _ = _settings(app_id, reader=True, path=native_path,
                                schema_source=schema_source)
        for key in KEYS[app_id]:
            if key in values: continue
            value = settings.get_user_value(key)
            if value is not None:
                values[key] = _bounded(app_id, key, value)
    _write(destination / 'import.json', {'schema': SCHEMA, 'app_id': app_id,
            'values': values}, exclusive=True)
    return {'keys': len(values), 'native_preferences_retained': True}

def apply(app_id, directory, *, schema_source=None):
    """Apply only absent sandbox user values; interruption is safe to retry."""
    reader_id(app_id)
    if os.environ.get('GSETTINGS_BACKEND') != 'keyfile':
        raise PreferencesError('Private application preference storage is not configured.')
    directory = Path(directory)
    _directory(directory)
    payload, digest = _read(directory / 'import.json')
    if set(payload) != {'schema', 'app_id', 'values'} or payload['schema'] != SCHEMA or payload['app_id'] != app_id:
        raise PreferencesError('The preference handoff does not belong to this application.')
    values = payload['values']
    if not isinstance(values, dict) or set(values) - set(KEYS[app_id]):
        raise PreferencesError('The preference handoff contains unapproved keys.')
    marker = directory / 'applied.json'
    if marker.exists() or marker.is_symlink():
        completed, _ = _read(marker)
        if completed != {'schema': SCHEMA, 'app_id': app_id, 'import_sha256': digest}:
            raise PreferencesError('The preference completion record needs review.')
        return {'applied': 0, 'already_complete': True}
    settings, schema = _settings(app_id, schema_source=schema_source)
    Gio, GLib = _gio()
    prepared = {}
    for key, record in values.items():
        if (not isinstance(record, dict) or set(record) != {'type', 'text'}
                or record['type'] != KEYS[app_id][key] or not isinstance(record['text'], str)
                or len(record['text'].encode()) > 131072):
            raise PreferencesError('A preference handoff value is invalid.')
        try:
            variant = GLib.Variant.parse(GLib.VariantType.new(record['type']), record['text'], None, None)
        except GLib.Error as error:
            raise PreferencesError('A preference handoff value could not be read.') from error
        _bounded(app_id, key, variant)
        if not schema.get_key(key).range_check(variant):
            raise PreferencesError('A preference is outside the installed schema range.')
        prepared[key] = variant
    settings.delay()
    try:
        count = 0
        for key, value in prepared.items():
            if settings.get_user_value(key) is None:
                if not settings.is_writable(key) or not settings.set_value(key, value):
                    raise PreferencesError('A preference could not be imported; existing data is preserved.')
                count += 1
        settings.apply(); Gio.Settings.sync()
    except Exception:
        settings.revert()
        raise
    _write(marker, {'schema': SCHEMA, 'app_id': app_id, 'import_sha256': digest})
    return {'applied': count, 'already_complete': False}

def apply_current():
    info = configparser.ConfigParser(interpolation=None, strict=True)
    info.read('/.flatpak-info')
    app_id = info.get('Application', 'name', fallback='')
    reader_id(app_id)
    root = Path(os.environ.get('XDG_DATA_HOME', ''))
    if not root.is_absolute() or '..' in root.parts:
        raise PreferencesError('The application data location is invalid.')
    current = root
    _directory(current)
    for name in ('state', 'luma', 'app-preferences', app_id):
        current = current / name
        if not current.exists() and not current.is_symlink():
            return {'applied': 0, 'no_native_snapshot': True}
        _directory(current)
    return apply(app_id, current)

if __name__ == '__main__':
    try:
        apply_current()
    except Exception:
        from luma_appkit.migration_startup import show_failure
        show_failure('Your preferences and documents are safe. Review the application data handoff in Depot before continuing.')
        raise SystemExit(1)
    if len(sys.argv) < 2:
        raise SystemExit('An application command is required.')
    os.execvp(sys.argv[1], sys.argv[1:])
