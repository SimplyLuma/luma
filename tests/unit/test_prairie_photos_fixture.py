# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/prairie-core'))
from prairie_apps.photos_fixture import FixtureSource, FixtureThumbnailCache

FIXTURE = ROOT / 'tests/fixtures/photos-v70.json'


class FixtureTests(unittest.TestCase):
    def test_exact_opening_sample_and_filtered_counts(self):
        source = FixtureSource(FIXTURE)
        self.assertEqual(len(source.rows()), 22)
        self.assertEqual([source.asset(str(i)).display_name for i in range(5)],
                         ['Writing at home','Laptop on the boardwalk','Terrace, morning','At the desk','On the way in'])
        self.assertEqual(len(source.rows('fav')), 6)
        self.assertEqual(len(source.rows('walls')), 4)
        self.assertEqual(len(source.rows('launch')), 11)
        self.assertEqual(source.rows('deleted'), ())
        self.assertEqual([p.id for p in source.rows(query='oakland')], ['0','14'])
        self.assertEqual([p.id for p in source.rows(query='PINK COAT')], ['17'])
        self.assertEqual(source.labels('0')['day_subtitle'], 'Tuesday, September 22')

    def test_page_filters_before_limit_and_cancellation(self):
        source = FixtureSource(FIXTURE)
        page, total = source.page(collection='favorites',offset=2,limit=2)
        self.assertEqual([p.id for p in page], ['6','9'])
        self.assertEqual(total, 6)
        self.assertEqual(source.page(cancelled=lambda: True), ((),0))
        with self.assertRaises(ValueError):
            source.page(limit=0)

    def test_all_fixture_changes_are_in_memory_and_do_not_touch_real_stores(self):
        before = FIXTURE.read_bytes()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'HOME':directory,'XDG_DATA_HOME':directory+'/data','XDG_CACHE_HOME':directory+'/cache'}), \
            patch('prairie_apps.photos_backend.PhotoLibrary.__init__', side_effect=AssertionError('real library opened')):
            source = FixtureSource(FIXTURE)
            source.scan_all()
            source.sources()
            source.set_favorite('0',False)
            source.update_adjustments('0',{'e':10})
            album=source.create_album('Proof')
            source.add_to_album(album.id,('0',))
            source.trash_copies(('fixture-copy-0',))
            self.assertEqual([p.id for p in source.rows('deleted')], ['0'])
            source.restore_copies(('fixture-copy-0',))
            self.assertFalse(source.asset('0').deleted)
            self.assertEqual(source.adjustments('0'), {'e':10})
            self.assertEqual(FixtureThumbnailCache.lookup(source.asset('0').copies[0]),source.asset('0').path)
            for operation in (source.import_files,source.export_assets,source.add_source,source.delete_copies_permanently):
                with self.assertRaises(PermissionError):
                    operation()
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(FIXTURE.read_bytes(), before)
        reopened=FixtureSource(FIXTURE)
        self.assertTrue(reopened.asset('0').favorite)
        self.assertEqual(reopened.adjustments('0'), {})

    def test_fixture_rejects_duplicate_ids_and_asset_escape(self):
        for mutation in ('duplicate','escape'):
            with tempfile.TemporaryDirectory() as directory:
                document=json.loads(FIXTURE.read_text())
                if mutation=='duplicate':
                    document['photos'].append(document['photos'][0])
                else:
                    document['photos'][0]['image']='../../real-data.jpg'
                path=Path(directory)/'fixture.json'
                path.write_text(json.dumps(document))
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    FixtureSource(path)


if __name__ == '__main__':
    unittest.main()
