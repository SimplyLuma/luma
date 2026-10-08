# SPDX-License-Identifier: Apache-2.0
"""Per-user native application data handoff; no root or arbitrary paths.

The broker authenticates the running sandbox before calling migrate(). Approved profile subdirectories are
committed by rename inside the already mounted XDG roots. Native inputs are retained;
existing sandbox data is never replaced by a stale operating-system copy.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time

# Relative paths within the authenticated user's home, never request arguments.
PATHS = {
    'org.projectluma.Depot': (('.local/state/luma/depot', 'data/state/luma/depot'),),
    'org.projectluma.Displays': (('.local/state/luma/displays.json', 'data/state/luma'),),
    'org.projectluma.Imager': (('.cache/luma-imager', 'cache/luma-imager'),),
    # Provider-owned EDS/calendar and host alarm scheduling are not copied.
    # These are the actual per-app durable UI stores, not whole XDG roots.
    'org.projectluma.Calendar': (('.local/state/luma/calendar-backups', 'data/state/luma/calendar-backups'),),
    'org.projectluma.Clock': (('.local/share/prairie/clock', 'data/prairie/clock'),),
    'org.projectluma.Weather': (('.local/share/prairie/weather', 'data/prairie/weather'),
                              ('.cache/prairie-weather', 'cache/prairie-weather')),
    # Phone shares only its two maintained host-family directories. The
    # sandbox must not fork call history or favourite/block policy.
    'org.projectluma.Phone': (),
    # Conversation, attachment, transport and encryption state stays in the
    # authenticated native MessagesHost; first launch records only readiness.
    'org.projectluma.Messages': (),
    # The signed Connect UI uses authenticated host methods. Enrollment,
    # device identity and synchronization stores never enter its profile.
    'org.projectluma.Connect': (),
    # Charlie UI and its native MailHost engine retain one narrow mailbox
    # database/WAL namespace; credentials remain in the native keyring.
    'org.projectluma.Charlie': (),
    # Recordings remain user documents at Music/Voice Memos; copying them to
    # an application profile would fork or orphan the visible library.
    'org.projectluma.VoiceMemos': (),
    # Photos and Camera deliberately share the host photo catalogue and user
    # pictures. A per-sandbox copy would fork albums and leave new captures
    # invisible to the other application.
    'org.projectluma.Photos': (),
    'org.projectluma.Camera': (),
    'org.projectluma.Leaf': (('.local/share/leaf', 'data/leaf'),),
    'org.projectluma.Notes': (('.local/share/luma/notes', 'data/luma/notes'),),
    # EDS books/tasks remain host-owned; only per-app sharing/local recovery
    # data changes ownership. Connect enrollment/device identity is not copied.
    'org.projectluma.Contacts': (('.local/share/luma/contacts', 'data/luma/contacts'),),
    'org.projectluma.Tasks': (('.local/share/luma/tasks', 'data/luma/tasks'),),
    # The sessions tree contains live Unix sockets/anonymous bridge state, not
    # the browser profile. It stays native; the sandbox creates its own session.
    'com.rhyme.viola': (('.local/share/viola-luma/profile', 'data/viola-luma/profile'),
                         ('.config/viola', 'config/viola')),
    'org.projectluma.Tide': (('.local/share/luma-tide', 'data/luma-tide'),
                           ('.cache/luma-tide', 'cache/luma-tide')),
    'org.projectluma.Viewer': (('.local/share/luma-viewer', 'data/luma-viewer'),
                             ('.cache/luma-viewer', 'cache/luma-viewer')),
    # Selected documents and adjacent recovery files remain user documents.
    'org.projectluma.Write': (),
    'org.projectluma.Grid': (('.local/share/luma/documents', 'data/luma/documents'),),
    'org.projectluma.Stage': (),
    'org.projectluma.Session': (('.local/share/luma-session/instruments', 'data/luma-session/instruments'),),
    'org.projectluma.Reel': (('.local/share/reel', 'data/reel'),
                           ('.local/state/reel', 'data/state/reel'),
                           ('.cache/reel', 'cache/reel')),
}
COLLABORATION_SOURCE = '.local/share/luma/connect/collaboration.sqlite3'
COLLABORATION_TARGET = 'data/luma/connect'
PREFERENCE_APPS = frozenset({'org.projectluma.Write', 'org.projectluma.Grid',
                           'org.projectluma.Stage', 'org.projectluma.Session', 'org.projectluma.Reel'})
PREFERENCE_SOURCE = '@app-preferences'

def _approved(app_id):
    paths = dict(PATHS[app_id])
    if app_id in ('org.projectluma.Notes', 'org.projectluma.Tasks'):
        paths[COLLABORATION_SOURCE] = COLLABORATION_TARGET
    if app_id in PREFERENCE_APPS:
        paths[PREFERENCE_SOURCE] = 'data/state/luma/app-preferences/' + app_id
    return paths

def _collaboration_tree(source, destination, uid, app_id, cancelled, records):
    """Filter a consistent backup, never a whole Connect identity directory."""
    from luma_installer.collaboration_migration import export_collaboration_cache
    before = _plain(source, uid=uid)
    destination.mkdir(mode=0o700)
    snapshot = destination / '.consistent-input.sqlite3'
    original = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)
    backup = sqlite3.connect(snapshot)
    try:
        original.backup(backup, pages=128,
                        progress=lambda *_: (_ for _ in ()).throw(MigrationError('Migration cancelled')) if cancelled() else None)
        if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise MigrationError('The collaboration cache could not be checked.')
    finally:
        backup.close(); original.close()
    try:
        counts = export_collaboration_cache(snapshot, destination / 'collaboration.sqlite3',
                                            app_id, cancelled=cancelled)
        after = _plain(source, uid=uid)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise MigrationError('The collaboration cache changed ownership during migration.')
        records.append({'path': str(source), 'filtered_collaboration': counts,
                        'credentials_copied': False})
    finally:
        snapshot.unlink()


def _collaboration_needs_review(source, home, uid, app_id):
    """A later empty host cache cannot invalidate an already completed import.

    A successful profile is never recopied or replaced. Relevant native rows
    that were not imported still require review, including recipient history;
    another app's rows do not create missing data for this app.
    """
    from luma_installer.collaboration_migration import KINDS, TABLES
    before = _plain(source, uid=uid)
    for ancestor in (source.parent, *source.parent.parents):
        if ancestor == home: break
        _plain(ancestor, directory=True, uid=uid)
    database = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        database.execute('PRAGMA query_only=ON'); database.execute('BEGIN')
        for table, columns in TABLES.items():
            actual = tuple(row[1] for row in database.execute('PRAGMA table_info(' + table + ')'))
            if actual != columns:
                raise MigrationError('The native collaboration cache needs schema review.')
        kinds = KINDS[app_id]
        parameters = ','.join('?' for _ in kinds)
        relevant = any(database.execute('SELECT 1 FROM ' + table +
                       ' WHERE kind IN (' + parameters + ') LIMIT 1', kinds).fetchone()
                       for table in ('documents', 'invitations'))
        relevant = relevant or database.execute('SELECT 1 FROM recipients LIMIT 1').fetchone()
        # An orphaned snapshot has no reliable app-kind association.
        relevant = relevant or database.execute('SELECT 1 FROM snapshots s LEFT JOIN documents d '
                        'ON s.id=d.id WHERE d.id IS NULL LIMIT 1').fetchone()
        after = _plain(source, uid=uid)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise MigrationError('The native collaboration cache changed during review.')
        return bool(relevant)
    finally:
        database.close()

def _display_state_tree(source, destination, uid, cancelled, records):
    """Import exactly the named file, never its shared native state parent."""
    before = _plain(source, uid=uid)
    if before.st_size > 1048576:
        raise MigrationError('The display names file needs review before importing.')
    if cancelled(): raise MigrationError('Migration cancelled; native data is intact.')
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened=os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):
            raise MigrationError('The display names file changed while opening.')
        with os.fdopen(fd,'rb',closefd=False) as stream: data=stream.read(1048577)
    finally: os.close(fd)
    after=_plain(source,uid=uid)
    if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)!=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns):
        raise MigrationError('Display names were edited during migration.')
    if len(data)>1048576 or not isinstance(json.loads(data),dict):
        raise MigrationError('Display names need review before importing.')
    destination.mkdir(mode=0o700)
    target=destination/'displays.json'
    with target.open('xb') as stream: stream.write(data); stream.flush();os.fsync(stream.fileno())
    target.chmod(0o600)
    records.append({'path':str(source),'destination':'displays.json','sha256':hashlib.sha256(data).hexdigest()})


def _depot_state_tree(source, destination, uid, cancelled, records):
    """Retain the two UI stores without importing host authority or siblings."""
    from dataclasses import fields
    from luma_installer.depot_app_history import Entry, Pause
    _plain(source, directory=True, uid=uid)
    destination.mkdir(mode=0o700)
    for name, limit in (('app-history.json', 4 * 1024 * 1024), ('held-updates.json', 262144)):
        path = source / name
        if not (path.exists() or path.is_symlink()):
            continue
        if cancelled(): raise MigrationError('Migration cancelled; native data is intact.')
        before = _plain(path, uid=uid)
        if before.st_size > limit:
            raise MigrationError('Depot state needs review before importing.')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise MigrationError('Depot state changed while opening.')
            with os.fdopen(fd, 'rb', closefd=False) as stream: data = stream.read(limit + 1)
        finally: os.close(fd)
        after = _plain(path, uid=uid)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise MigrationError('Depot state changed during migration.')
        value = json.loads(data)
        if len(data) > limit or not isinstance(value, dict):
            raise MigrationError('Depot state needs review before importing.')
        if name == 'app-history.json':
            if set(value) - {'entries', 'paused'}:
                raise MigrationError('Depot history has unsupported data.')
            for key, cls in (('entries', Entry), ('paused', Pause)):
                items = value.get(key, [])
                if not isinstance(items, list) or len(items) > 500:
                    raise MigrationError('Depot history needs review before importing.')
                allowed = {field.name for field in fields(cls)}
                if any(not isinstance(item, dict) or set(item) - allowed for item in items):
                    raise MigrationError('Depot history has unsupported fields.')
                for item in items:
                    if (not isinstance(item.get('app_id'), str) or not item['app_id']
                            or len(item['app_id']) > 256
                            or type(item.get('at')) is not int or item['at'] <= 0):
                        raise MigrationError('Depot history has invalid application records.')
                    for field, field_value in item.items():
                        if field == 'at': continue
                        if field == 'automatic':
                            if type(field_value) is not bool:
                                raise MigrationError('Depot history has invalid automatic state.')
                        elif not isinstance(field_value, str) or len(field_value) > (4000 if field == 'notes' else 256):
                            raise MigrationError('Depot history has invalid text fields.')
        else:
            allowed = {'approved', 'notified', 'available_notified', 'completed_notified'}
            if set(value) - allowed or any(not isinstance(items, list) or len(items) > 200
                    or any(not isinstance(item, str) or len(item) > 4096 for item in items)
                    for items in value.values()):
                raise MigrationError('Depot notification state has unsupported data.')
        target = destination / name
        with target.open('xb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        target.chmod(0o600)
        records.append({'path': str(path), 'destination': name,
                        'sha256': hashlib.sha256(data).hexdigest(),
                        'approval_cache_authoritative': False})

class MigrationError(ValueError):
    pass

def _plain(path, *, directory=False, uid=None):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (uid is not None and info.st_uid != uid):
        raise MigrationError('Application data has an unsafe owner or link.')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise MigrationError('Application data is not a regular file or directory.')
    return info

def _parents(path, home, uid):
    current = home
    _plain(current, directory=True, uid=uid)
    for part in path.relative_to(home).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _plain(current, directory=True, uid=uid)
        else:
            current.mkdir(mode=0o700)

def _browser_runtime_links(source, uid):
    """Retain Chromium's closed-process singleton links only in the original.

    They point to process-private sockets, lock owners and readiness cookies;
    none is durable profile data. All other links retain the usual refusal.
    """
    result = {}
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        path = source / name
        try: info = path.lstat()
        except FileNotFoundError: continue
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != uid:
            raise MigrationError('The browser singleton state needs review before import.')
        value = os.readlink(path)
        if len(value) > 4096:
            raise MigrationError('The browser singleton state is invalid.')
        if name == 'SingletonLock':
            host, separator, number = value.rpartition('-')
            if not separator or host != platform.node() or not number.isdecimal() or not 0 < int(number) <= 2147483647:
                raise MigrationError('The browser lock owner needs review before import.')
            try: os.kill(int(number), 0)
            except ProcessLookupError: pass
            except PermissionError:
                raise MigrationError('The browser lock owner cannot be checked; native data is preserved.')
            else:
                raise MigrationError('Close the native browser before importing its profile.')
        result[name] = (info.st_dev, info.st_ino, info.st_mtime_ns, value)
    return result

def _copy_tree(source, destination, uid, cancelled, records, *, runtime_links=None):
    _plain(source, directory=True, uid=uid)
    destination.mkdir(mode=0o700)
    children = sorted(source.iterdir())
    databases = set()
    for child in children:
        if runtime_links is not None and child.name in runtime_links:
            continue
        if child.is_dir() and not child.is_symlink():
            continue
        _plain(child, uid=uid)
        with child.open('rb') as stream:
            if stream.read(16) == b'SQLite format 3\0':
                databases.add(child.name)
    for child in children:
        if cancelled():
            raise MigrationError('Application data migration was cancelled. The original is intact.')
        target = destination / child.name
        if runtime_links is not None and child.name in runtime_links:
            continue
        if child.is_dir() and not child.is_symlink():
            _copy_tree(child, target, uid, cancelled, records)
            continue
        before = _plain(child, uid=uid)
        if any(child.name == database + suffix for database in databases for suffix in ('-wal', '-shm', '-journal')):
            continue  # SQLite backup below includes the committed WAL consistently.
        if child.name in databases:
            original = sqlite3.connect(child.as_uri() + '?mode=ro', uri=True, timeout=5)
            backup = sqlite3.connect(target)
            try:
                original.backup(backup, pages=128,
                                progress=lambda *_: (_ for _ in ()).throw(MigrationError('Migration cancelled')) if cancelled() else None)
                if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise MigrationError('The application database could not be checked.')
                backup.commit()
                records.append({'path': str(child), 'sqlite_schema': backup.execute('PRAGMA user_version').fetchone()[0]})
            finally:
                backup.close()
                original.close()
            os.chmod(target, 0o600)
        else:
            fd = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise MigrationError('Application data changed while it was being opened.')
                with os.fdopen(fd, 'rb', closefd=False) as read, target.open('xb') as write:
                    shutil.copyfileobj(read, write, 1024 * 1024)
                    write.flush(); os.fsync(write.fileno())
            finally:
                os.close(fd)
            os.chmod(target, 0o600)
        after = _plain(child, uid=uid)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise MigrationError('Application data changed during migration. Try again after closing the native app.')
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        if child.name not in databases and (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise MigrationError('Application data was edited during migration. Close the native app and try again.')
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        records.append({'path': str(child), 'destination': str(target.relative_to(destination)),
                        'sha256': digest})

def _atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix=path.name + '.writing-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', closefd=False) as stream:
            json.dump(value, stream, sort_keys=True); stream.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)


def _tree_digest(root):
    records = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise MigrationError('A recovery snapshot contains a link.')
        key = path.relative_to(root).as_posix()
        if path.is_dir(): records[key] = {'directory': True}
        else:
            _plain(path, uid=os.getuid())
            with path.open('rb') as stream:
                records[key] = {'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
    return records

def _publish_ready(state, app_id, receipt, result, uid):
    # Host sync needs a bounded completion marker, not an attachment inventory.
    # The full transaction/recovery receipt remains immutable evidence here.
    ready = state / (app_id + '.ready.json')
    if ready.exists() or ready.is_symlink(): _plain(ready, uid=uid)
    with receipt.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    _atomic_json(ready, {'schema': 'org.projectluma.app-data-ready/v1',
                        'app_id': app_id, 'result': 'PASS',
                        'receipt_sha256': digest,
                        'completed_unix': result['completed_unix'],
                        'collaboration_cache_migrated': bool(result.get('collaboration_cache_migrated'))})


def migrate(app_id, home=None, *, cancelled=lambda: False):
    if app_id not in PATHS:
        raise MigrationError('This application has no approved native data mapping.')
    home = Path(home or Path.home()).absolute()
    uid = os.getuid()
    state = home / '.local/state/luma/app-migration'
    _parents(state, home, uid)
    fd = os.open(state / (app_id + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
            raise MigrationError('Migration lock is unsafe.')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise MigrationError('This application is already migrating.')
        receipt = state / (app_id + '.json')
        if receipt.exists():
            _plain(receipt, uid=uid)
            previous = json.loads(receipt.read_text())
            if previous.get('result') == 'PASS' and previous.get('app_id') == app_id:
                if (app_id in ('org.projectluma.Notes', 'org.projectluma.Tasks')
                        and (home / COLLABORATION_SOURCE).exists()
                        and not previous.get('collaboration_cache_migrated')
                        and previous.get('collaboration_source_state') != 'absent'
                        and _collaboration_needs_review(home / COLLABORATION_SOURCE,
                                                       home, uid, app_id)):
                    raise MigrationError('An earlier import needs collaboration-data review in Depot. Existing application data is preserved.')
                _publish_ready(state, app_id, receipt, previous, uid)
                return previous
        target = home / '.var/app' / app_id
        _parents(target, home, uid)
        pending = state / (app_id + '.pending.json')
        approved = _approved(app_id)
        if pending.exists():
            _plain(pending, uid=uid)
            plan = json.loads(pending.read_text())
            name = plan.get('snapshot', '')
            if (plan.get('app_id') != app_id or not name.startswith(app_id + '-')
                    or '/' in name or '..' in name):
                raise MigrationError('The recovery record is invalid.')
            snapshot = state / 'recovery' / name
            _plain(snapshot, directory=True, uid=uid)
            for item in plan['items']:
                if approved.get(item['source']) != item['destination']:
                    raise MigrationError('The recovery record does not match approved paths.')
                if _tree_digest(snapshot / item['destination']) != item['digest']:
                    raise MigrationError('The immutable recovery snapshot changed.')
        else:
            recovery = state / 'recovery'; _parents(recovery, home, uid)
            snapshot = Path(tempfile.mkdtemp(prefix=app_id + '-', dir=recovery))
            plan = {'schema': 'org.projectluma.app-data-migration/v1', 'app_id': app_id,
                    'snapshot': snapshot.name, 'native_data_retained': True,
                    'completed_unix': None, 'items': [], 'records': []}
            if app_id in ('org.projectluma.Notes', 'org.projectluma.Tasks'):
                # Preserve whether this shared host cache existed at handoff.
                # Later host activity can create history for a different app.
                cache_source = home / COLLABORATION_SOURCE
                plan['collaboration_source_state'] = (
                    'present' if cache_source.exists() or cache_source.is_symlink() else 'absent')
            try:
                for relative, destination in approved.items():
                    source = home / relative
                    is_preferences = relative == PREFERENCE_SOURCE
                    if not is_preferences and not (source.exists() or source.is_symlink()): continue
                    is_collaboration = relative == COLLABORATION_SOURCE
                    is_display_state = app_id == 'org.projectluma.Displays' and relative == '.local/state/luma/displays.json'
                    is_depot_state = app_id == 'org.projectluma.Depot' and relative == '.local/state/luma/depot'
                    if is_collaboration or is_display_state: _plain(source, uid=uid)
                    ancestors = ([] if is_preferences else
                                 [source.parent, *source.parent.parents] if is_collaboration or is_display_state else [source, *source.parents])
                    for ancestor in ancestors:
                        if ancestor == home: break
                        _plain(ancestor, directory=True, uid=uid)
                    out = target / destination
                    _parents(out.parent, home, uid)
                    if out.exists() or out.is_symlink():
                        _plain(out, directory=True, uid=uid)
                        if any(out.iterdir()):
                            raise MigrationError('Existing sandbox data is preserved. Review migration in Depot before importing native data.')
                    saved = snapshot / destination
                    saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if is_collaboration:
                        _collaboration_tree(source, saved, uid, app_id, cancelled, plan['records'])
                        plan['collaboration_cache_migrated'] = True
                    elif is_display_state:
                        _display_state_tree(source, saved, uid, cancelled, plan['records'])
                    elif is_depot_state:
                        _depot_state_tree(source, saved, uid, cancelled, plan['records'])
                    elif is_preferences:
                        from luma_installer.app_preferences import capture
                        saved.mkdir(mode=0o700)
                        captured = capture(app_id, saved)
                        plan['records'].append({'source': PREFERENCE_SOURCE, 'preferences': captured})
                    elif app_id == 'com.rhyme.viola':
                        links = _browser_runtime_links(source, uid)
                        _copy_tree(source, saved, uid, cancelled, plan['records'], runtime_links=links)
                        if _browser_runtime_links(source, uid) != links:
                            raise MigrationError('The browser runtime state changed during import.')
                        plan['records'].append({'path': str(source), 'native_runtime_links_retained': sorted(links)})
                    else:
                        _copy_tree(source, saved, uid, cancelled, plan['records'])
                    plan['items'].append({'source': relative, 'destination': destination,
                                          'digest': _tree_digest(saved)})
                if cancelled(): raise MigrationError('Migration cancelled; native data is intact.')
                _atomic_json(snapshot / 'MANIFEST.json', plan)
                _atomic_json(pending, plan)
            except Exception:
                if not pending.exists(): shutil.rmtree(snapshot)
                raise
        # XDG roots are already mounted by Flatpak. Publish their application
        # subdirectories, retaining those root inodes and the recovery snapshot.
        for item in plan['items']:
            out = target / item['destination']
            _parents(out.parent, home, uid)
            if out.exists() or out.is_symlink():
                _plain(out, directory=True, uid=uid)
                if _tree_digest(out) == item['digest']:
                    continue  # A prior interrupted attempt already committed this path.
                if any(out.iterdir()):
                    raise MigrationError('Sandbox data changed during recovery; it is preserved for review.')
                out.rmdir()
            if cancelled(): raise MigrationError('Migration cancelled; its recovery snapshot is retained.')
            activation = Path(tempfile.mkdtemp(prefix='.luma-activation-', dir=out.parent))
            activation.rmdir()
            try:
                shutil.copytree(snapshot / item['destination'], activation)
                if _tree_digest(activation) != item['digest']:
                    raise MigrationError('The prepared application data failed its digest check.')
                for file in activation.rglob('*'):
                    if file.is_file():
                        with file.open('rb') as stream: os.fsync(stream.fileno())
                os.rename(activation, out)
                directory = os.open(out.parent, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                if activation.exists(): shutil.rmtree(activation)
        result = dict(plan, result='PASS', completed_unix=time.time())
        _atomic_json(receipt, result)
        _publish_ready(state, app_id, receipt, result, uid)
        pending.unlink()
        return result
    finally:
        os.close(fd)
