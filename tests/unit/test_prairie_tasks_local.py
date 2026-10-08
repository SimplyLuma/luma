# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/prairie-core"))
from prairie_apps.tasks_local import LocalTasks, backup_source
from prairie_apps.tasks_backend import TasksRepository


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "tasks/local.json"
        self.store = LocalTasks(self.path)

    def test_read_does_not_create_any_files(self):
        self.assertEqual(self.store.read()["tasks"], {})
        self.assertFalse(self.path.parent.exists())

    def test_persistence_and_undo_preserve_other_tasks_and_fields(self):
        undo = self.store.edit("a", {"flag": True})
        LocalTasks(self.path).edit("a", {"who": ["me", "other"]})
        self.store.edit("b", {"flag": True})
        undo()
        self.assertEqual(self.store.read()["tasks"], {"a": {"who": ["me", "other"]}, "b": {"flag": True}})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_undo_rejects_concurrent_same_field_edit(self):
        undo = self.store.edit("a", {"flag": True})
        LocalTasks(self.path).edit("a", {"flag": False})
        with self.assertRaises(RuntimeError): undo()
        self.assertFalse(self.store.read()["tasks"]["a"]["flag"])

    def test_invalid_existing_file_is_never_replaced(self):
        self.path.parent.mkdir(); self.path.write_text('{"version":1,"tasks":{"a":{"who":7}}}')
        original = self.path.read_bytes()
        with self.assertRaises(ValueError): self.store.edit("a", {"flag": True})
        self.assertEqual(self.path.read_bytes(), original)

    def test_failed_atomic_replace_leaves_previous_content(self):
        self.store.edit("a", {"flag": True}); original = self.path.read_bytes()
        with patch('prairie_apps.tasks_local.os.replace', side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.store.edit("a", {"flag": False})
        self.assertEqual(self.path.read_bytes(), original)

    def test_local_backup_preserves_original_unknown_data_once(self):
        self.path.parent.mkdir()
        original = {"version": 1, "unknown": {"keep": 7}, "tasks": {"a": {"flag": False, "other": "keep"}}}
        self.path.write_text(json.dumps(original))
        self.store.edit("a", {"flag": True})
        backup = self.path.parent / "backups/local-original.json"
        saved = backup.read_bytes()
        self.store.edit("a", {"who": ["me"]})
        self.assertEqual(json.loads(saved), original)
        self.assertEqual(backup.read_bytes(), saved)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.read()["unknown"], {"keep": 7})
        self.assertEqual(self.store.read()["tasks"]["a"]["other"], "keep")

    def test_failed_local_backup_prevents_edit(self):
        self.path.parent.mkdir()
        self.path.write_text('{"version":1,"tasks":{"a":{"flag":false}}}')
        original = self.path.read_bytes()
        with patch('prairie_apps.tasks_local.os.link', side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.store.edit("a", {"flag": True})
        self.assertEqual(self.path.read_bytes(), original)

    def test_collection_backup_is_complete_once_and_private(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name}):
            backup_source("source", ["first", "second"])
            backup_source("source", ["changed"])
        files = list((Path(self.directory.name) / 'luma/tasks/backups').glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())["components"], ["first", "second"])
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)

    def test_failed_backup_prevents_eds_mutation(self):
        repo = TasksRepository(); client = Mock(); repo.clients['source'] = client
        client.get_object_list_sync.return_value = (False, [])
        with patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name}):
            with self.assertRaises(RuntimeError): repo._delete('source', 'task')
        client.remove_object_sync.assert_not_called()


if __name__ == '__main__': unittest.main()
