# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from luma_tide.recency import AlbumRecency


class RecencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'tide' / 'album-history.json'
        self.history = AlbumRecency(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_read_only_start_and_stable_unplayed_order(self):
        self.history.load()
        albums = tuple(SimpleNamespace(id=i) for i in ('a', 'b', 'c'))
        self.assertEqual(self.history.order(albums), albums)
        self.assertFalse(self.path.parent.exists())
        self.history.record('b', 10)
        self.history.record('c', 20)
        self.assertEqual([a.id for a in self.history.order(albums)], ['c', 'b', 'a'])
        restored = AlbumRecency(self.path)
        restored.load()
        self.assertEqual(restored.order(albums), self.history.order(albums))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_unknown_fields_and_first_edit_backup_survive(self):
        self.path.parent.mkdir()
        original = '{"extra":{"keep":true},"albums":{"old":1}}\n'
        self.path.write_text(original)
        self.history.record('a', 10)
        self.history.record('b', 20)
        self.assertEqual(json.loads(self.path.read_text()),
                         {'extra': {'keep': True}, 'albums': {'old': 1, 'a': 10, 'b': 20}})
        backup = self.path.with_suffix('.json.before-first-write')
        self.assertEqual(backup.read_text(), original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_malformed_or_failed_backup_never_overwrites(self):
        self.path.parent.mkdir()
        self.path.write_text('damaged history')
        with self.assertRaises(ValueError):
            self.history.record('a')
        self.assertEqual(self.path.read_text(), 'damaged history')
        self.path.write_text('{"albums":{"old":1}}')
        with patch('luma_tide.recency.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.history.record('a')
        self.assertEqual(json.loads(self.path.read_text()), {'albums': {'old': 1}})
        self.assertFalse(self.path.with_suffix('.json.before-first-write').exists())

    def test_two_writers_preserve_each_others_albums(self):
        def record(index):
            AlbumRecency(self.path).record(str(index), index)
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(record, range(20)))
        self.assertEqual(json.loads(self.path.read_text())['albums'],
                         {str(i): i for i in range(20)})

    def test_failed_replace_keeps_existing_file(self):
        self.history.record('old', 1)
        with patch('luma_tide.recency.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.history.record('new', 2)
        self.assertEqual(json.loads(self.path.read_text()), {'albums': {'old': 1}})
        self.assertEqual(list(self.path.parent.glob('.album-history-*')), [])
