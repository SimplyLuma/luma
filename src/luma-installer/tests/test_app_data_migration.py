# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
import shutil
from pathlib import Path
import sqlite3
import socket
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('app_data_migration', Path(__file__).parents[1] / 'luma_installer/app_data_migration.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class NativeDataMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.native = self.home / '.local/share/luma/notes'
        self.native.mkdir(parents=True)
    def tearDown(self): self.tmp.cleanup()
    def target(self): return self.home / '.var/app/org.projectluma.Notes'

    def test_depot_preserves_only_ui_history_and_announcements(self):
        native = self.home / '.local/state/luma/depot'; native.mkdir(parents=True)
        history = {'entries': [{'app_id':'org.projectluma.Notes','name':'Notes',
                    'from_version':'A','to_version':'B','at':123,'automatic':False}],
                   'paused': [{'app_id':'org.projectluma.Notes','name':'Notes',
                    'reverted_from_version':'B','reverted_from_commit':'a'*64,'at':124}]}
        held = {'approved':['cached-ui-only'], 'notified':['update:B']}
        for name, value in [('app-history.json', history), ('held-updates.json', held)]:
            (native/name).write_text(json.dumps(value))
        (native/'countme.json').write_text('{"private":"host-count"}')
        (native/'enrollment-token').write_text('host-only-token')
        result = module.migrate('org.projectluma.Depot', self.home)
        target = self.home / '.var/app/org.projectluma.Depot/data/state/luma/depot'
        self.assertEqual({p.name for p in target.iterdir()}, {'app-history.json','held-updates.json'})
        for name in ('app-history.json','held-updates.json'):
            self.assertEqual((native/name).read_bytes(), (target/name).read_bytes())
            self.assertEqual((target/name).stat().st_mode & 0o777, 0o600)
        self.assertEqual((native/'enrollment-token').read_text(),'host-only-token')
        self.assertTrue(all(not row['approval_cache_authoritative'] for row in result['records']))
        (target/'app-history.json').write_text('{"retained":"sandbox edit"}')
        module.migrate('org.projectluma.Depot', self.home)
        self.assertEqual((target/'app-history.json').read_text(),'{"retained":"sandbox edit"}')

    def test_depot_invalid_or_linked_ui_state_never_activates_profile(self):
        native = self.home / '.local/state/luma/depot'; native.mkdir(parents=True)
        history = native/'app-history.json'
        for value in ({'entries':[{'app_id':'Notes','at':1,'token':'secret'}]},
                      {'entries':[{'app_id':'Notes','at':True}]},
                      {'entries':[{'app_id':'Notes','at':1,'notes':{'token':'secret'}}]}):
            history.write_text(json.dumps(value))
            with self.assertRaises(module.MigrationError): module.migrate('org.projectluma.Depot', self.home)
            self.assertFalse((self.home/'.var/app/org.projectluma.Depot/data/state/luma/depot').exists())
        history.unlink(); history.symlink_to(self.native/'outside')
        (self.native/'outside').write_text('{"entries":[]}')
        with self.assertRaises(module.MigrationError): module.migrate('org.projectluma.Depot', self.home)
        self.assertTrue(history.is_symlink())

    def migrate(self, **kwargs): return module.migrate('org.projectluma.Notes', self.home, **kwargs)
    def collaboration(self):
        from luma_installer.collaboration_migration import SCHEMA
        path = self.home / module.COLLABORATION_SOURCE
        path.parent.mkdir(parents=True)
        database = sqlite3.connect(path)
        database.execute('PRAGMA journal_mode=WAL')
        for statement in SCHEMA: database.execute(statement)
        for kind in ('note', 'task', 'list'):
            database.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                             (kind, 'https://hub.test', 'host-device', kind, kind+'-local', 7, 'viewer', '{}'))
            database.execute('INSERT INTO snapshots VALUES(?,?)', (kind, json.dumps({'kind':kind})))
        database.execute('CREATE TABLE tokens(secret TEXT)')
        database.execute("INSERT INTO tokens VALUES('host-only credential')")
        database.commit()
        (path.parent / 'device.json').write_text('host enrollment')
        return database

    def test_profile_and_filtered_collaboration_commit_together_without_enrollment(self):
        (self.native / 'note.txt').write_text('Native shared note')
        source = self.collaboration()
        result = self.migrate()
        cache = self.target() / 'data/luma/connect/collaboration.sqlite3'
        exported = sqlite3.connect(cache)
        self.assertEqual(exported.execute('SELECT id,local_id,role,revision FROM documents').fetchall(),
                         [('note','note-local','viewer',7)])
        self.assertEqual(exported.execute('SELECT id FROM snapshots').fetchall(), [('note',)])
        self.assertIsNone(exported.execute("SELECT name FROM sqlite_master WHERE name='tokens'").fetchone())
        exported.close()
        self.assertTrue(result['collaboration_cache_migrated'])
        ready_path = self.home / '.local/state/luma/app-migration/org.projectluma.Notes.ready.json'
        ready = json.loads(ready_path.read_text())
        self.assertEqual(ready['schema'], 'org.projectluma.app-data-ready/v1')
        self.assertEqual(ready['app_id'], 'org.projectluma.Notes')
        self.assertEqual(ready['result'], 'PASS')
        self.assertLess(ready_path.stat().st_size, 1024)
        self.assertEqual(ready_path.stat().st_mode & 0o777, 0o600)
        receipt = ready_path.with_name('org.projectluma.Notes.json')
        self.assertEqual(ready['receipt_sha256'], module.hashlib.sha256(receipt.read_bytes()).hexdigest())
        self.assertEqual(source.execute('SELECT count(*) FROM documents').fetchone()[0],3)
        self.assertFalse(cache.with_name('device.json').exists())
        self.assertFalse(cache.with_name('.consistent-input.sqlite3').exists())
        self.assertEqual((self.target()/'data/luma/notes/note.txt').read_text(),'Native shared note')
        source.close()

    def test_bad_collaboration_schema_leaves_profile_unactivated(self):
        (self.native / 'note.txt').write_text('Original note')
        source = self.collaboration()
        source.execute('DROP TABLE snapshots'); source.commit()
        with self.assertRaises(ValueError): self.migrate()
        self.assertFalse((self.home/'.local/state/luma/app-migration/org.projectluma.Notes.ready.json').exists())
        self.assertFalse((self.target()/'data/luma/notes').exists())
        self.assertEqual((self.native/'note.txt').read_text(),'Original note')
        self.assertEqual(source.execute('SELECT count(*) FROM documents').fetchone()[0],3)
        source.close()

    def test_completed_import_survives_later_empty_native_cache_without_recopy(self):
        (self.native/'note.txt').write_text('First native data')
        first = self.migrate()
        receipt = self.home/'.local/state/luma/app-migration/org.projectluma.Notes.json'
        original_receipt = receipt.read_bytes()
        active = self.target()/'data/luma/notes/note.txt'
        active.write_text('Independent application edit')
        cache = self.collaboration()
        cache.execute('DELETE FROM documents'); cache.execute('DELETE FROM snapshots')
        cache.commit()
        self.assertEqual(self.migrate(), first)
        self.assertEqual(receipt.read_bytes(),original_receipt)
        self.assertEqual(active.read_text(),'Independent application edit')
        self.assertFalse((self.target()/'data/luma/connect/collaboration.sqlite3').exists())
        # Existing data of another app still does not invalidate Notes.
        cache.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                      ('task','https://hub.test','device','task','local',1,'viewer','{}'))
        cache.execute('INSERT INTO snapshots VALUES(?,?)',('task','{"kind":"task"}'))
        cache.commit()
        self.assertEqual(self.migrate(), first)
        cache.close()

    def test_completed_import_still_refuses_missed_native_collaboration_rows(self):
        self.migrate()
        # Legacy receipts do not prove whether the source was absent at import.
        receipt = self.home/'.local/state/luma/app-migration/org.projectluma.Notes.json'
        old = json.loads(receipt.read_text()); old.pop('collaboration_source_state')
        receipt.write_text(json.dumps(old))
        cache = self.collaboration()
        with self.assertRaises(module.MigrationError): self.migrate()
        cache.execute('DELETE FROM documents'); cache.execute('DELETE FROM snapshots')
        cache.execute('INSERT INTO recipients VALUES(?,?,?,?,?,?)',
                      ('https://hub.test','device','account','{"handle":"peer"}',1,1.0))
        cache.commit()
        with self.assertRaises(module.MigrationError): self.migrate()
        cache.execute('DELETE FROM recipients'); cache.execute('DROP TABLE invitations'); cache.commit()
        with self.assertRaises(module.MigrationError): self.migrate()
        self.assertFalse((self.target()/'data/luma/connect/collaboration.sqlite3').exists())
        cache.close()

    def test_absent_source_receipt_retains_later_global_history_without_reimport(self):
        first = self.migrate()
        self.assertEqual(first['collaboration_source_state'], 'absent')
        cache = self.collaboration()
        cache.execute('INSERT INTO recipients VALUES(?,?,?,?,?,?)',
                      ('https://hub.test','device','account','{"handle":"later-task-peer"}',1,1.0))
        cache.commit()
        self.assertEqual(self.migrate(), first)
        self.assertFalse((self.target()/'data/luma/connect/collaboration.sqlite3').exists())
        self.assertEqual(cache.execute('SELECT count(*) FROM recipients').fetchone()[0],1)
        cache.close()

    def test_interrupted_cache_commit_resumes_original_filtered_snapshot(self):
        from unittest.mock import patch
        source = self.collaboration()
        (self.native / 'note.txt').write_text('Original note')
        rename = module.os.rename
        def interrupt(old,new):
            if str(new).endswith('data/luma/connect'): raise OSError('Power interruption')
            return rename(old,new)
        with patch.object(module.os,'rename',side_effect=interrupt):
            with self.assertRaises(OSError): self.migrate()
        self.assertFalse((self.home/'.local/state/luma/app-migration/org.projectluma.Notes.ready.json').exists())
        source.execute("UPDATE documents SET role='editor', revision=9 WHERE kind='note'"); source.commit()
        result = self.migrate()
        exported = sqlite3.connect(self.target()/'data/luma/connect/collaboration.sqlite3')
        self.assertEqual(exported.execute('SELECT role,revision FROM documents').fetchall(), [('viewer',7)])
        exported.close();source.close()
        self.assertTrue(result['collaboration_cache_migrated'])
    def test_display_names_copy_only_fixed_file_not_sibling_credentials(self):
        source = self.home / '.local/state/luma/displays.json'
        source.parent.mkdir(parents=True)
        original = b'{"names":{"one":"Personal display"},"dismissed":[]}'
        source.write_bytes(original)
        (source.parent / 'identity.json').write_text('Never copied')
        module.migrate('org.projectluma.Displays', self.home)
        target = self.home / '.var/app/org.projectluma.Displays/data/state/luma'
        self.assertEqual({p.name for p in target.iterdir()}, {'displays.json'})
        self.assertEqual((target / 'displays.json').read_bytes(), original)
        self.assertEqual(source.read_bytes(), original)
        (target / 'displays.json').write_text('{"names":{"one":"New sandbox name"}}')
        module.migrate('org.projectluma.Displays', self.home)
        self.assertIn('New sandbox name', (target / 'displays.json').read_text())
        self.assertEqual(source.read_bytes(), original)

    def test_display_symlink_and_invalid_names_refused_without_activation(self):
        source = self.home / '.local/state/luma/displays.json'
        source.parent.mkdir(parents=True)
        secret = self.home / 'private'; secret.write_text('{"secret":true}')
        source.symlink_to(secret)
        with self.assertRaises(module.MigrationError): module.migrate('org.projectluma.Displays', self.home)
        self.assertEqual(secret.read_text(), '{"secret":true}')
        source.unlink(); source.write_text('invalid json')
        with self.assertRaises((module.MigrationError, ValueError)): module.migrate('org.projectluma.Displays', self.home)
        self.assertFalse((self.home / '.var/app/org.projectluma.Displays/data/state/luma').exists())

    def test_photo_family_keeps_one_host_catalogue(self):
        library = self.home / '.local/share/luma-photos'
        library.mkdir(parents=True)
        connection = sqlite3.connect(library / 'library.sqlite3')
        connection.execute('CREATE TABLE captures (name TEXT)')
        connection.execute("INSERT INTO captures VALUES ('Camera image')")
        connection.commit(); connection.close()
        for app in ('org.projectluma.Photos', 'org.projectluma.Camera'):
            result = module.migrate(app, self.home)
            self.assertTrue(result['native_data_retained'])
            self.assertFalse((self.home / '.var/app' / app / 'data/luma-photos').exists())
        connection = sqlite3.connect(library / 'library.sqlite3')
        self.assertEqual(connection.execute('SELECT name FROM captures').fetchone()[0], 'Camera image')
        connection.close()

    def test_leaf_library_preserves_original_and_new_sandbox_edits(self):
        library = self.home / '.local/share/leaf'
        library.mkdir(parents=True)
        (library / 'annotations.json').write_text('original highlights')
        module.migrate('org.projectluma.Leaf', self.home)
        imported = self.home / '.var/app/org.projectluma.Leaf/data/leaf/annotations.json'
        self.assertEqual(imported.read_text(), 'original highlights')
        imported.write_text('new independently updated highlights')
        module.migrate('org.projectluma.Leaf', self.home)
        self.assertEqual(imported.read_text(), 'new independently updated highlights')
        self.assertEqual((library / 'annotations.json').read_text(), 'original highlights')
    def test_committed_wal_and_attachment_are_preserved(self):
        connection = sqlite3.connect(self.native / 'notes.sqlite3')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE notes (body TEXT)')
        connection.execute("INSERT INTO notes VALUES ('Persisted native note')"); connection.commit()
        (self.native / 'picture.png').write_bytes(b'unchanged-picture')
        result = self.migrate()
        imported = sqlite3.connect(self.target() / 'data/luma/notes/notes.sqlite3')
        self.assertEqual(imported.execute('SELECT body FROM notes').fetchone()[0], 'Persisted native note')
        self.assertEqual(imported.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        imported.close(); connection.close()
        self.assertEqual((self.native / 'picture.png').read_bytes(), b'unchanged-picture')
        self.assertEqual((self.target() / 'data/luma/notes/picture.png').read_bytes(), b'unchanged-picture')
        self.assertTrue(result['native_data_retained'])
    def test_repeat_and_os_rollback_cannot_replace_newer_app_data(self):
        (self.native / 'note.txt').write_text('Native old text')
        self.migrate()
        target = self.target() / 'data/luma/notes/note.txt'
        target.write_text('Edited in independently updated application')
        (self.native / 'note.txt').write_text('Stale native rollback text')
        self.migrate()
        self.assertEqual(target.read_text(), 'Edited in independently updated application')
    def test_existing_sandbox_never_overwritten(self):
        self.target().mkdir(parents=True)
        existing = self.target() / 'data/luma/notes'
        existing.mkdir(parents=True)
        (existing / 'existing').write_text('Keep this')
        (self.native / 'note').write_text('Native note')
        with self.assertRaises(module.MigrationError): self.migrate()
        self.assertEqual((existing / 'existing').read_text(), 'Keep this')
    def test_cancel_does_not_activate_or_remove_native_data(self):
        (self.native / 'note').write_text('Retain native')
        with self.assertRaises(module.MigrationError): self.migrate(cancelled=lambda: True)
        self.assertFalse((self.target() / 'data/luma/notes').exists())
        self.assertEqual((self.native / 'note').read_text(), 'Retain native')
    def test_linked_source_or_destination_refused(self):
        secret = self.home / 'secret'; secret.write_text('Private')
        (self.native / 'note').symlink_to(secret)
        with self.assertRaises(module.MigrationError): self.migrate()
        self.assertFalse((self.target() / 'data/luma/notes').exists())
        (self.native / 'note').unlink()
        parent = self.home / '.var/app'; parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.target())
        self.target().symlink_to(self.native)
        with self.assertRaises(module.MigrationError): self.migrate()
        self.assertEqual(secret.read_text(), 'Private')
    def test_atomic_commit_recovers_missing_side_receipt(self):
        (self.native / 'note').write_text('Original')
        self.migrate()
        receipt = self.home / '.local/state/luma/app-migration/org.projectluma.Notes.json'
        # A committed data path with a missing final receipt is recovered only
        # from the exact retained snapshot, never from edited native bytes.
        receipt.unlink()
        recovery = next((self.home / '.local/state/luma/app-migration/recovery').iterdir())
        pending = self.home / '.local/state/luma/app-migration/org.projectluma.Notes.pending.json'
        pending.write_text((recovery / 'MANIFEST.json').read_text())
        (self.native / 'note').write_text('Native changed after snapshot')
        self.migrate()
        self.assertEqual((self.target() / 'data/luma/notes/note').read_text(), 'Original')
        self.assertEqual(json.loads(receipt.read_text())['result'], 'PASS')
    def test_existing_mounted_xdg_root_inode_stays_visible(self):
        root = self.target() / 'data'; root.mkdir(parents=True)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        before = os.fstat(fd).st_ino
        (self.native / 'note').write_text('Visible through original mount')
        try:
            self.migrate()
            self.assertEqual(root.stat().st_ino, before)
            file = os.open('luma/notes/note', os.O_RDONLY, dir_fd=fd)
            try: self.assertEqual(os.read(file, 100), b'Visible through original mount')
            finally: os.close(file)
        finally: os.close(fd)
    def test_multi_path_recovery_keeps_retained_snapshot(self):
        native = self.home / '.local/share/luma-tide'; native.mkdir()
        (native / 'account').write_text('Retained library')
        cache = self.home / '.cache/luma-tide'; cache.mkdir(parents=True)
        (cache / 'cover').write_bytes(b'cover')
        from unittest.mock import patch
        rename = module.os.rename
        def interrupted(source, target):
            if str(target).endswith('cache/luma-tide'): raise OSError('Power interruption')
            return rename(source, target)
        with patch.object(module.os, 'rename', side_effect=interrupted):
            with self.assertRaises(OSError): module.migrate('org.projectluma.Tide', self.home)
        (native / 'account').write_text('Changed native copy')
        result = module.migrate('org.projectluma.Tide', self.home)
        destination = self.home / '.var/app/org.projectluma.Tide'
        self.assertEqual((destination / 'data/luma-tide/account').read_text(), 'Retained library')
        self.assertEqual((destination / 'cache/luma-tide/cover').read_bytes(), b'cover')
        self.assertEqual(result['result'], 'PASS')
    def test_weather_places_preference_and_cache_keep_exact_paths(self):
        native = self.home/'.local/share/prairie/weather'; native.mkdir(parents=True)
        saved = '{"places":[],"preferences":{"units":"imperial"}}'
        (native/'places.json').write_text(saved)
        cache = self.home/'.cache/prairie-weather'; cache.mkdir(parents=True)
        (cache/'city.json').write_text('{"cached":true}')
        result = module.migrate('org.projectluma.Weather', self.home)
        destination = self.home/'.var/app/org.projectluma.Weather'
        self.assertEqual((destination/'data/prairie/weather/places.json').read_text(),saved)
        self.assertEqual((destination/'cache/prairie-weather/city.json').read_text(),'{"cached":true}')
        self.assertEqual((native/'places.json').read_text(),saved)
        self.assertEqual(result['result'],'PASS')

    def test_messages_first_launch_preserves_host_database_and_credentials(self):
        native = self.home / '.local/share/luma/messages'
        native.mkdir(parents=True)
        database = native / 'messages.sqlite3'
        database.write_bytes(b'owned-host-conversation-and-encryption-state')
        enrollment = self.home / '.local/share/luma/connect/device.json'
        enrollment.parent.mkdir(parents=True)
        enrollment.write_bytes(b'owned-host-enrollment')
        first = module.migrate('org.projectluma.Messages', self.home)
        self.assertEqual(first['items'], [])
        destination = self.home / '.var/app/org.projectluma.Messages'
        self.assertEqual(list(destination.iterdir()), [])
        ready = self.home / '.local/state/luma/app-migration/org.projectluma.Messages.ready.json'
        receipt = ready.with_name('org.projectluma.Messages.json')
        self.assertEqual(json.loads(ready.read_text())['result'], 'PASS')
        self.assertEqual(json.loads(ready.read_text())['receipt_sha256'], module.hashlib.sha256(receipt.read_bytes()).hexdigest())
        before = receipt.read_bytes(), ready.read_bytes()
        self.assertEqual(module.migrate('org.projectluma.Messages', self.home), first)
        self.assertEqual((receipt.read_bytes(), ready.read_bytes()), before)
        self.assertEqual(database.read_bytes(), b'owned-host-conversation-and-encryption-state')
        self.assertEqual(enrollment.read_bytes(), b'owned-host-enrollment')
        self.assertEqual(list(destination.iterdir()), [])

    def test_voice_memo_documents_are_not_copied_to_an_empty_profile(self):
        library = self.home/'Music/Voice Memos'; library.mkdir(parents=True)
        (library/'existing.ogg').write_bytes(b'owned-user-document')
        result = module.migrate('org.projectluma.VoiceMemos', self.home)
        self.assertEqual(result['items'],[])
        self.assertEqual((library/'existing.ogg').read_bytes(),b'owned-user-document')
        self.assertEqual(list((self.home/'.var/app/org.projectluma.VoiceMemos').iterdir()),[])

    def test_viewer_recents_positions_and_thumbnails_survive_without_copying_documents(self):
        data = self.home/'.local/share/luma-viewer'; data.mkdir(parents=True)
        cache = self.home/'.cache/luma-viewer/thumbnails'; cache.mkdir(parents=True)
        document = self.home/'Documents/owned.pdf'; document.parent.mkdir()
        document.write_bytes(b'original user document')
        recents = json.dumps({'files':[{'path':str(document),'page':27,'zoom':1.25}]})
        (data/'recents.json').write_text(recents)
        (cache/'owned.png').write_bytes(b'owned thumbnail')
        first = module.migrate('org.projectluma.Viewer', self.home)
        target = self.home/'.var/app/org.projectluma.Viewer'
        self.assertEqual((target/'data/luma-viewer/recents.json').read_text(),recents)
        self.assertEqual((target/'cache/luma-viewer/thumbnails/owned.png').read_bytes(),b'owned thumbnail')
        self.assertFalse((target/'Documents').exists())
        self.assertEqual(document.read_bytes(),b'original user document')
        receipt = self.home/'.local/state/luma/app-migration/org.projectluma.Viewer.json'
        before = receipt.read_bytes()
        (target/'data/luma-viewer/recents.json').write_text('{"files":[]}')
        self.assertEqual(module.migrate('org.projectluma.Viewer',self.home),first)
        self.assertEqual(receipt.read_bytes(),before)
        self.assertEqual((target/'data/luma-viewer/recents.json').read_text(),'{"files":[]}')
        self.assertEqual((data/'recents.json').read_text(),recents)

    def test_viewer_linked_cache_refuses_before_any_profile_activation(self):
        data = self.home/'.local/share/luma-viewer'; data.mkdir(parents=True)
        (data/'recents.json').write_text('{"files":[]}')
        cache = self.home/'.cache/luma-viewer'; cache.mkdir(parents=True)
        (cache/'foreign').symlink_to(data/'recents.json')
        with self.assertRaises(module.MigrationError): module.migrate('org.projectluma.Viewer',self.home)
        self.assertFalse((self.home/'.var/app/org.projectluma.Viewer/data/luma-viewer').exists())
        self.assertTrue((cache/'foreign').is_symlink())

    def test_creative_preference_failure_never_activates_copied_document_data(self):
        from unittest.mock import patch
        native = self.home/'.local/share/luma/documents'; native.mkdir(parents=True)
        (native/'owned.ods').write_bytes(b'original owned sheet')
        def interrupted(app, destination):
            (destination/'partial').write_bytes(b'incomplete preferences')
            raise OSError('native preference service unavailable')
        with patch('luma_installer.app_preferences.capture', side_effect=interrupted):
            with self.assertRaises(OSError): module.migrate('org.projectluma.Grid', self.home)
        self.assertFalse((self.home/'.var/app/org.projectluma.Grid/data/luma/documents').exists())
        self.assertFalse((self.home/'.local/state/luma/app-migration/org.projectluma.Grid.json').exists())
        self.assertFalse((self.home/'.local/state/luma/app-migration/org.projectluma.Grid.pending.json').exists())
        self.assertEqual((native/'owned.ods').read_bytes(),b'original owned sheet')

    def test_fixed_maps_and_users_are_isolated(self):
        with self.assertRaises(module.MigrationError): module.migrate('../secret', self.home)
        second = self.home / 'another-user'; second.mkdir()
        first = self.migrate(); other = module.migrate('org.projectluma.Notes', second)
        self.assertEqual(first['app_id'], other['app_id'])
        self.assertTrue((second / '.var/app/org.projectluma.Notes').is_dir())
        self.assertFalse((second / '.var/app/org.projectluma.Notes/data/luma/notes').exists())

    def test_viola_profile_only_ignores_live_bridge_socket_sibling(self):
        browser = self.home / '.local/share/viola-luma'
        profile = browser / 'profile'; profile.mkdir(parents=True)
        (profile / 'Preferences').write_text('{"browser":{"show_home_button":true}}')
        sessions = browser / 'sessions'; sessions.mkdir()
        (sessions / 'private-bridge-state').write_text('native-only-session')
        bridge = socket.socket(socket.AF_UNIX)
        try:
            bridge.bind(str(sessions / 'bridge.sock'))
            result = module.migrate('com.rhyme.viola', self.home)
            destination = self.home / '.var/app/com.rhyme.viola/data/viola-luma'
            self.assertEqual((destination / 'profile/Preferences').read_bytes(), (profile / 'Preferences').read_bytes())
            self.assertFalse((destination / 'sessions').exists())
            self.assertTrue((sessions / 'bridge.sock').exists())
            self.assertEqual((sessions / 'private-bridge-state').read_text(), 'native-only-session')
            self.assertTrue(result['native_data_retained'])
        finally:
            bridge.close()

    def test_closed_viola_singleton_links_are_runtime_state_not_profile_data(self):
        import platform
        profile = self.home/'.local/share/viola-luma/profile'; profile.mkdir(parents=True)
        (profile/'Preferences').write_text('{"retained":true}')
        (profile/'SingletonLock').symlink_to(platform.node()+'-2147483647')
        (profile/'SingletonSocket').symlink_to('/tmp/closed-owned-browser/SingletonSocket')
        (profile/'SingletonCookie').symlink_to('owned-runtime-cookie')
        result = module.migrate('com.rhyme.viola',self.home)
        target = self.home/'.var/app/com.rhyme.viola/data/viola-luma/profile'
        self.assertEqual((target/'Preferences').read_bytes(),(profile/'Preferences').read_bytes())
        self.assertEqual({p.name for p in target.iterdir()},{'Preferences'})
        self.assertTrue(all((profile/n).is_symlink() for n in ('SingletonLock','SingletonSocket','SingletonCookie')))
        self.assertTrue(result['native_data_retained'])

    def test_live_viola_lock_and_unapproved_links_refuse_without_activation(self):
        import platform
        profile = self.home/'.local/share/viola-luma/profile'; profile.mkdir(parents=True)
        (profile/'Preferences').write_text('{"retained":true}')
        lock = profile/'SingletonLock'; lock.symlink_to(platform.node()+'-'+str(os.getpid()))
        with self.assertRaises(module.MigrationError): module.migrate('com.rhyme.viola',self.home)
        target = self.home/'.var/app/com.rhyme.viola/data/viola-luma/profile'
        self.assertFalse(target.exists())
        lock.unlink()
        (profile/'user-data-link').symlink_to(profile/'Preferences')
        with self.assertRaises(module.MigrationError): module.migrate('com.rhyme.viola',self.home)
        self.assertFalse(target.exists())

    def test_viola_foreign_malformed_and_changed_singleton_state_preserves_original(self):
        from unittest.mock import patch
        import platform
        profile = self.home/'.local/share/viola-luma/profile'; profile.mkdir(parents=True)
        (profile/'Preferences').write_text('{"retained":true}')
        lock = profile/'SingletonLock'
        target = self.home/'.var/app/com.rhyme.viola/data/viola-luma/profile'
        for value in ('foreign-host-2147483647',platform.node()+'-0',platform.node()+'-not-a-pid',platform.node()+'-2147483648'):
            lock.symlink_to(value)
            with self.subTest(value=value), self.assertRaises(module.MigrationError):
                module.migrate('com.rhyme.viola',self.home)
            self.assertFalse(target.exists()); self.assertEqual(os.readlink(lock),value)
            lock.unlink()
        cookie = profile/'SingletonCookie'; cookie.symlink_to('first-owned-cookie')
        copy = module._copy_tree
        def changed(*args, **kwargs):
            copy(*args, **kwargs)
            cookie.unlink(); cookie.symlink_to('changed-owned-cookie')
        with patch.object(module,'_copy_tree',side_effect=changed), self.assertRaises(module.MigrationError):
            module.migrate('com.rhyme.viola',self.home)
        self.assertFalse(target.exists())
        self.assertEqual((profile/'Preferences').read_text(),'{"retained":true}')
        self.assertEqual(os.readlink(cookie),'changed-owned-cookie')
