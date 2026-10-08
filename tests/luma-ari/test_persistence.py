# SPDX-License-Identifier: Apache-2.0
"""All writes use temporary Ari-owned files, never the user's stores."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ari.persistence import backup_path, update_json
from ari.store import Store


class Persistence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_unedited_fields_survive_and_original_backup_is_never_replaced(self):
        path = self.root / 'config.json'
        original = b'{"brain":"local","future":{"custom":true},"active_model":"first"}'
        path.write_bytes(original)
        update_json(path, {'brain': 'openrouter'})
        self.assertEqual(backup_path(path).read_bytes(), original)
        external = json.loads(path.read_text())
        external['active_model'] = 'external'
        path.write_text(json.dumps(external))
        update_json(path, {'brain': 'local'}, defaults={'active_model': 'downloaded'})
        self.assertEqual(json.loads(path.read_text()), {'brain': 'local', 'future': {'custom': True}, 'active_model': 'external'})
        self.assertEqual(backup_path(path).read_bytes(), original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup_path(path).stat().st_mode & 0o777, 0o600)

    def test_invalid_config_is_preserved(self):
        path = self.root / 'config.json'
        path.write_bytes(b'{broken')
        with self.assertRaises(ValueError):
            update_json(path, {'brain': 'local'})
        self.assertEqual(path.read_bytes(), b'{broken')
        self.assertFalse(backup_path(path).exists())

    def test_explicit_removal_preserves_other_fields(self):
        path = self.root / 'config.json'
        path.write_text('{"active_model":"first","brain":"local"}')
        update_json(path, {}, removed=('active_model',))
        self.assertEqual(json.loads(path.read_text()), {'brain': 'local'})

    def test_database_backup_includes_wal_and_stays_at_original_state(self):
        path = self.root / 'ari.db'
        first = Store(path)
        first.db.execute('PRAGMA journal_mode=WAL')
        conversation = first.new_conversation('Original')
        first.add_message(conversation, 'user', 'Preserve me')
        second = Store(path)
        second.add_message(conversation, 'assistant', 'New reply')
        third = Store(path)
        backup = sqlite3.connect(backup_path(path))
        try:
            self.assertEqual(backup.execute('SELECT content FROM messages').fetchall(), [('Preserve me',)])
            self.assertEqual(len(third.messages(conversation)), 2)
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        finally:
            backup.close()
            first.db.close()
            second.db.close()
            third.db.close()


if __name__ == '__main__':
    unittest.main()
