# SPDX-License-Identifier: Apache-2.0
"""Native clients for Hub's cross-account documents; never stores credentials.

Revision conflicts retain the native edited document and its remote ancestor.
Network work is done off GTK's main loop by the app/Connect sync owner.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from urllib.parse import quote
from .connect_sync import ConnectError, HubClient, HubResponseError, connect_data_directory, hub_url, load_identity

PREFIX = '/api/hub/sync/collaboration'


def transport(environment, timeout):
    env = os.environ if environment is None else environment
    if env.get('FLATPAK_ID'):
        from .collaboration_transport import BrokerHTTP
        return BrokerHTTP()
    return HubClient(timeout=timeout)


def public_name(person):
    name = str(person.get('display_name') or '').strip()
    handle = str(person.get('handle') or '').strip().lstrip('@')
    return name if name and '@' not in name else ('@' + handle if handle else 'Luma user')


class PeopleDirectory:
    def __init__(self, environment=None, http=None):
        self.identity = load_identity(environment)
        self.http = http or transport(environment, 8)
        if self.identity is None:
            raise ConnectError('Sign in to Luma Connect to find people on Luma.')
        self.address = hub_url(self.identity.hub)

    def lookup(self, handle):
        from .messages_luma import handle_from
        handle = handle_from(handle)
        if not handle:
            raise ValueError('Enter a valid Luma username.')
        result = self.http.get_json(self.address + '/api/hub/sync/people/' + quote(handle, safe=''), token=self.identity.token)
        if result.get('blocked_by_you'):
            raise ConnectError('You blocked this person.')
        if not result.get('account'):
            raise ConnectError('This person could not be found on Luma.')
        return result

    def friends(self):
        return self.http.get_json(self.address + '/api/hub/sync/chat/accepted', token=self.identity.token).get('items', [])

    def discover(self, identifiers):
        hashes = []
        for kind, value in identifiers:
            normalized = str(value).strip().lower() if kind == 'email' else str(value).strip()
            # A phone lookup is meaningful only for an already normalized E.164
            # identifier. No guessed country codes and no address book upload.
            if kind == 'phone' and not (normalized.startswith('+') and normalized[1:].isdigit()):
                continue
            if normalized:
                hashes.append(hashlib.sha256(('luma-discovery-v1:' + normalized).encode()).hexdigest())
        if not hashes:
            return []
        return self.http.post_json(self.address + '/api/hub/sync/identity/discover', {'hashes': hashes}, token=self.identity.token).get('matches', [])


class CollaborationClient:
    def __init__(self, environment=None, http=None):
        self.identity = load_identity(environment)
        self.http = http or transport(environment, 12)
        if self.identity is None:
            raise ConnectError('Sign in to Luma Connect to collaborate.')
        self.address = hub_url(self.identity.hub)

    def get(self, suffix):
        return self.http.get_json(self.address + PREFIX + suffix, token=self.identity.token)

    def post(self, suffix, data):
        return self.http.post_json(self.address + PREFIX + suffix, data, token=self.identity.token)

    def documents(self):
        return self.get('/documents').get('documents', [])

    def read(self, document):
        return self.get('/documents/' + str(uuid.UUID(document)))

    def create(self, kind, content, document=None):
        return self.post('/documents', {'id': document or str(uuid.uuid4()), 'kind': kind, 'content': content})

    def operation(self, document, action, data):
        if action not in ('invite', 'accept', 'revoke', 'content', 'comments', 'remove', 'restore', 'transfer'):
            raise ValueError('Unknown collaboration operation.')
        return self.post('/documents/' + str(uuid.UUID(document)) + '/' + action, data)


class CollaborationCache:
    """Transactional local mapping; its base is the last acknowledged content."""
    def __init__(self, environment=None, path=None, *, read_only=False):
        self.path = Path(path) if path else connect_data_directory(environment) / 'collaboration.sqlite3'
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ConnectError('Collaboration state must not be a symbolic link.')
        if read_only:
            if not self.path.is_file():
                raise ConnectError('No shared documents have been received yet.')
            self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
            self.db.row_factory = sqlite3.Row
            return
        self.db = sqlite3.connect(self.path)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,hub TEXT NOT NULL,device TEXT NOT NULL,kind TEXT NOT NULL,local_id TEXT NOT NULL,revision INTEGER NOT NULL,role TEXT NOT NULL,base TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,snapshot TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY,hub TEXT NOT NULL,device TEXT NOT NULL,kind TEXT NOT NULL,owner TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS recipients(hub TEXT NOT NULL,device TEXT NOT NULL,account TEXT NOT NULL,person TEXT NOT NULL,uses INTEGER NOT NULL,last_used REAL NOT NULL,PRIMARY KEY(hub,device,account))')
        self.db.commit()

    def close(self):
        self.db.close()

    def frequent(self, client):
        return [json.loads(row[0]) for row in self.db.execute('SELECT person FROM recipients WHERE hub=? AND device=? ORDER BY uses DESC,last_used DESC LIMIT 8', (client.address, client.identity.device_id))]

    def used_recipient(self, client, person):
        import time
        if not person.get('account') or not person.get('handle'):
            return
        clean = {key: person.get(key) for key in ('account', 'handle', 'display_name', 'hue')}
        with self.db:
            self.db.execute('INSERT INTO recipients VALUES(?,?,?,?,1,?) ON CONFLICT(hub,device,account) DO UPDATE SET person=excluded.person,uses=uses+1,last_used=excluded.last_used', (client.address, client.identity.device_id, clean['account'], json.dumps(clean), time.time()))

    def mappings(self, client, kind=None):
        rows = self.db.execute('SELECT * FROM documents WHERE hub=? AND device=?' + (' AND kind=?' if kind else ''), (client.address, client.identity.device_id, kind) if kind else (client.address, client.identity.device_id)).fetchall()
        return [dict(row) | {'base': json.loads(row['base'])} for row in rows]

    def remember_snapshot(self, client, snapshot):
        # Comments/membership acknowledgement cannot advance the content CAS
        # ancestor over an unsent local edit. The normal sync owns that step.
        old = self.db.execute('SELECT hub,device FROM documents WHERE id=?', (snapshot['id'],)).fetchone()
        if old is None or (old['hub'], old['device']) != (client.address, client.identity.device_id):
            raise ConnectError('This shared snapshot has no matching local document.')
        with self.db:
            self.db.execute('UPDATE snapshots SET snapshot=? WHERE id=?', (json.dumps(snapshot), snapshot['id']))
            self.db.execute('UPDATE documents SET role=? WHERE id=?', (snapshot['role'], snapshot['id']))

    def remember(self, client, snapshot, local_id):
        old = self.db.execute('SELECT hub,device FROM documents WHERE id=?', (snapshot['id'],)).fetchone()
        if old and (old['hub'], old['device']) != (client.address, client.identity.device_id):
            raise ConnectError('This shared identifier belongs to another Hub/device; its local state was retained.')
        snapshot = dict(snapshot)
        snapshot.setdefault('_sync_received_unix', time.time())
        with self.db:
            self.db.execute('INSERT INTO snapshots VALUES(?,?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot', (snapshot['id'], json.dumps(snapshot)))
            self.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,role=excluded.role,base=excluded.base', (snapshot['id'], client.address, client.identity.device_id, snapshot['kind'], local_id, snapshot['revision'], snapshot['role'], json.dumps(snapshot['content'], sort_keys=True)))

    def unchanged_snapshot(self, client, document):
        """Reuse content only after the fresh authorized listing agrees.

        Legacy Hubs without a generation still fetch normally. Periodic full
        reads refresh public member names, which can change outside document
        operations. No credential or write authorization is cached here.
        """
        generation = document.get('sync_generation')
        if type(generation) is not int or not 0 <= generation <= 9007199254740991:
            return None
        row = self.db.execute('SELECT s.snapshot FROM snapshots s JOIN documents d ON d.id=s.id WHERE d.id=? AND d.hub=? AND d.device=?',
                              (document['id'], client.address, client.identity.device_id)).fetchone()
        if row is None:
            return None
        try:
            snapshot = json.loads(row[0])
            age = time.time() - snapshot.get('_sync_received_unix', 0)
            if not 0 <= age < 60:
                return None
            if all(snapshot.get(key) == document.get(key) for key in ('id', 'kind', 'revision', 'role', 'sync_generation')):
                return snapshot
        except (ValueError, TypeError, AttributeError):
            pass
        return None


def note_content(note):
    if any(run.get('style') in ('file', 'image') for run in note.runs):
        raise ConnectError('Share a copy of this note: collaborative attachment transfer is not supported yet.')
    return {'title': note.display_title, 'body': note.body, 'runs': list(note.runs)}


def sync_notes_collaboration(*, environment=None, http=None, store_path=None):
    """Use the existing Connect live-change wakeup; no new resident poller."""
    from .notes_backend import NotesStore, NoteChangedError
    from .connect_sync import notes_store_path
    from .collaboration_ownership import profile_ready
    if not profile_ready('org.projectluma.Notes', environment):
        return {'updated': 0, 'conflicts': 0, 'deferred_until_first_launch': True}
    client = CollaborationClient(environment, http)
    try:
        documents = client.documents()
    except HubResponseError as error:
        if error.status == 404:  # Older Hub, personal sync remains supported.
            return {'updated': 0, 'conflicts': 0, 'unsupported': True}
        raise
    from .app_installs import NOTES, app_environment
    cache = CollaborationCache(app_environment(NOTES, environment))
    store = NotesStore(store_path or notes_store_path(environment))
    updated = conflicts = 0
    try:
        with cache.db:
            cache.db.execute('DELETE FROM invitations WHERE hub=? AND device=?', (client.address, client.identity.device_id))
            for pending in documents:
                if pending['kind'] == 'note' and not pending['accepted']:
                    cache.db.execute('INSERT INTO invitations VALUES(?,?,?,?,?)', (pending['id'], client.address, client.identity.device_id, pending['kind'], pending['owner']))
        mappings = {row['id']: row for row in cache.mappings(client, 'note')}
        accessible = {row['id'] for row in documents if row['kind'] == 'note' and row['accepted']}
        for document in documents:
            if document['id'] not in accessible:
                continue
            snapshot = cache.unchanged_snapshot(client, document) or client.read(document['id'])
            mapping = mappings.get(document['id'])
            if mapping:
                try:
                    note = store.get_note(mapping['local_id'])
                except KeyError:
                    continue  # Deleting a local copy cannot delete the shared original.
                local = note_content(note)
                if local != mapping['base']:
                    if snapshot['role'] not in ('owner', 'edit') or snapshot['revision'] != mapping['revision']:
                        # Preserve the unsent version as an ordinary native note,
                        # then show the accepted remote revision in the shared copy.
                        store.create_note(title=note.display_title + ' (unsent edits)', body=note.body, runs=note.runs)
                        conflicts += 1
                    else:
                        try:
                            snapshot = client.operation(snapshot['id'], 'content', {'revision': mapping['revision'], 'content': local})
                        except HubResponseError as error:
                            if error.status != 409:
                                raise
                            store.create_note(title=note.display_title + ' (unsent edits)', body=note.body, runs=note.runs)
                            conflicts += 1
                            snapshot = client.read(snapshot['id'])
                remote = snapshot['content']
                if note_content(note) != remote:
                    try:
                        store.update_note(note.id, title=remote['title'], body=remote['body'], runs=tuple(remote['runs']), expected=note)
                    except NoteChangedError:
                        # An edit made during HTTP remains the active local text;
                        # its old ancestor must remain unchanged for the next CAS.
                        continue
                    updated += 1
                local_id = note.id
            else:
                remote = snapshot['content']
                note = store.create_note(title=remote['title'], body=remote['body'], runs=tuple(remote['runs']))
                local_id = note.id
                updated += 1
            cache.remember(client, snapshot, local_id)
        # Retain revoked local documents as recoverable personal copies, but
        # stop granting edit rights through stale membership metadata.
        with cache.db:
            for id, mapping in mappings.items():
                if id not in accessible:
                    cache.db.execute('UPDATE documents SET role=? WHERE id=?', ('revoked', id))
        return {'updated': updated, 'conflicts': conflicts}
    finally:
        store.close()
        cache.close()
