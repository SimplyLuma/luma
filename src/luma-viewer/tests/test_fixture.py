# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from luma_viewer.fixture import FixtureRecents

class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'fixture.json'
    def fixture(self,files=None):
        self.path.write_text(json.dumps({'selected':'receipt','files':files or [dict(uid='receipt',name='Receipt.png',kind='PNG image',group='Today',where='~/Downloads',generated='receipt',width=900,height=1240)]}))
        return FixtureRecents(self.path)
    def test_virtual_files_and_positions_never_write(self):
        r=self.fixture();entry=r.entries[0];original=self.path.read_bytes()
        with patch('builtins.open',side_effect=AssertionError('unexpected disk access')):
            r.record(entry.path);r.remember_position(entry.path,page=2);r.save();r.store_thumbnail(entry.path,None)
            self.assertEqual(r.facts_for(entry.path).name,'Receipt.png')
        self.assertEqual(entry.page,2);self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['fixture.json'])
    def test_external_asset_rejected_before_reading_it(self):
        with self.assertRaisesRegex(ValueError,'stay beside'):
            self.fixture([dict(uid='receipt',name='Bad',group='Today',asset='../outside')])
    def test_real_files_cannot_be_recorded_or_opened(self):
        r=self.fixture()
        with self.assertRaisesRegex(ValueError,'real files'):r.record('/etc/passwd')
        with self.assertRaisesRegex(ValueError,'real files'):r.facts_for('/etc/passwd')
    def test_duplicate_ids_fail(self):
        file=dict(uid='receipt',name='a',group='Today')
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.fixture([file,file])

    def test_external_portrait_rejected_before_decoding(self):
        self.path.write_text(json.dumps({'selected':'receipt','files':[dict(uid='receipt',name='Receipt.png',group='Today')],
            'people':[dict(name='Person',asset='../outside')]}))
        with self.assertRaisesRegex(ValueError,'portraits must stay beside'):
            FixtureRecents(self.path)

class LeaseFixtureTests(unittest.TestCase):
    def test_pdf_has_three_pages_and_native_field_space(self):
        try:
            import gi
            gi.require_version('Poppler','0.18')
            from gi.repository import GLib,Poppler
        except (ImportError,ValueError):
            self.skipTest('Poppler is unavailable')
        from luma_viewer.fixture import lease_pdf
        fields=[];data=lease_pdf(fields=fields)
        document=Poppler.Document.new_from_bytes(GLib.Bytes.new(data),None)
        self.assertEqual(document.get_n_pages(),3)
        self.assertIn('Commercial Lease Renewal',document.get_page(0).get_text())
        self.assertEqual({field[1] for field in fields},{'tenant','orig','date'})
        for page,key,placeholder,x,y,width in fields:
            self.assertIn(page,(0,2));self.assertTrue(placeholder)
            self.assertGreaterEqual(x,76);self.assertLessEqual(x+width,536)
            self.assertGreaterEqual(y,72);self.assertLessEqual(y+24,720)
        # Field placeholders are editable overlays, not duplicated in the PDF.
        self.assertNotIn('Tenant name',document.get_page(0).get_text())
