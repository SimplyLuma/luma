# SPDX-License-Identifier: Apache-2.0
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from luma_viewer.recents import Recents

class RecentTests(unittest.TestCase):
    def test_preview_history_and_cache_are_separate_from_production(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            production = root / 'production' / 'luma-viewer'
            production.mkdir(parents=True)
            original = json.dumps({'version': 1, 'files': [{'path': '/real.pdf'}]})
            (production / 'recents.json').write_text(original)
            env = {'XDG_DATA_HOME': str(root / 'production'),
                   'XDG_CACHE_HOME': str(root / 'production-cache'),
                   'LUMA_VIEWER_PREVIEW': '1',
                   'LUMA_VIEWER_PREVIEW_STATE_ROOT': str(root / 'preview')}
            with patch.dict(os.environ, env):
                preview = Recents()
                self.assertEqual(preview.entries, [])
                self.assertEqual(preview.directory, root / 'preview/data/luma-viewer')
                self.assertEqual(preview.cache, root / 'preview/cache/luma-viewer/thumbnails')
                preview.record('/preview.pdf')
                self.assertTrue(preview.file.exists())
            self.assertEqual((production / 'recents.json').read_text(), original)
            with patch.dict(os.environ, env | {'LUMA_VIEWER_PREVIEW': ''}):
                self.assertEqual(Recents().entries[0].path, '/real.pdf')

    def test_corrupt_optional_state_does_not_crash_open(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for value in ([],None,{'files':None},{'files':[None,{'path':[]},{'path':'a','page':'bad'},
                            {'path':'a','opened_at':float('inf')}]}):
                (root/'recents.json').write_text(json.dumps(value))
                self.assertEqual(Recents(root,root/'cache').entries,[])
    def test_unwritable_storage_is_optional(self):
        with tempfile.TemporaryDirectory() as d:
            blocked=Path(d)/'file';blocked.write_text('existing')
            r=Recents(blocked,blocked/'cache')
            r.record('/a/document.pdf');r.remember_position('/a/document.pdf',page=3)
            self.assertEqual(r.entries[0].page,3)
            self.assertIsNone(r.store_thumbnail('/a/document.pdf',None))
            self.assertEqual(blocked.read_text(),'existing')

    def test_edit_preserves_unknown_fields_and_concurrent_changes_with_backup(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'recents.json'
            original={'version':7,'future':{'keep':True},'files':[
                {'path':'/document.pdf','page':1,'opened_at':1,'custom':'keep'}]}
            file.write_text(json.dumps(original));r=Recents(root,root/'cache')
            concurrent=json.loads(file.read_text());concurrent['files'][0]['zoom']=2.5
            concurrent['files'].append({'path':'/other.pdf','opened_at':2,'future':True})
            file.write_text(json.dumps(concurrent));before=file.read_bytes()
            r.remember_position('/document.pdf',page=3)
            saved=json.loads(file.read_text());row=saved['files'][0]
            self.assertEqual(row['page'],3);self.assertEqual(row['zoom'],2.5)
            self.assertEqual(row['custom'],'keep');self.assertEqual(saved['future'],{'keep':True})
            self.assertEqual(saved['version'],7);self.assertEqual(saved['files'][1],concurrent['files'][1])
            backup=root/'recents.json.before-lumaui';self.assertEqual(backup.read_bytes(),before)
            r.remember_position('/document.pdf',page=4)
            self.assertEqual(backup.read_bytes(),before)

    def test_corrupt_existing_store_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'recents.json';file.write_bytes(b'{unreadable')
            r=Recents(root,root/'cache');r.record('/document.pdf')
            self.assertEqual(file.read_bytes(),b'{unreadable')

    def test_position_update_does_not_restore_concurrently_forgotten_record(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'recents.json'
            file.write_text(json.dumps({'version':1,'files':[{'path':'/document.pdf'}]}))
            r=Recents(root,root/'cache');file.write_text(json.dumps({'version':1,'files':[]}))
            r.remember_position('/document.pdf',page=3)
            self.assertEqual(json.loads(file.read_text())['files'],[])

    def test_explicit_clear_removes_all_history_and_keeps_original_backup(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'recents.json'
            file.write_text(json.dumps({'version':1,'files':[{'path':'/a.pdf'}, {'future-record':True}]}))
            original=file.read_bytes();r=Recents(root,root/'cache');r.clear()
            self.assertEqual(json.loads(file.read_text())['files'],[])
            self.assertEqual((root/'recents.json.before-lumaui').read_bytes(),original)

    def test_backup_failure_leaves_existing_history_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'recents.json'
            file.write_text(json.dumps({'version':1,'files':[{'path':'/document.pdf','page':1}]}))
            original=file.read_bytes();r=Recents(root,root/'cache')
            with patch('luma_viewer.recents.os.link',side_effect=OSError('backup denied')):
                r.remember_position('/document.pdf',page=3)
            self.assertEqual(file.read_bytes(),original)
            self.assertFalse((root/'recents.json.before-lumaui').exists())
            self.assertEqual(list(root.glob('.recents-backup-*')),[])

if __name__=='__main__':unittest.main()
