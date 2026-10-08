# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/prairie-core'))
from prairie_apps.photos_fixture import FixtureSource
from prairie_apps.photos_state import PhotosState
from prairie_apps.photos_data import outer_corners,tile_layout,groups_for


class PhotosStateTests(unittest.TestCase):
    def setUp(self):
        self.source=FixtureSource(ROOT/'tests/fixtures/photos-v70.json')
        self.state=PhotosState(self.source)
        self.state.apply_page(self.state.read_page())

    def test_day_groups_and_short_rows_match_spec(self):
        groups=groups_for(self.state.records,'days',self.state.now,self.source)
        self.assertEqual([len(g.records) for g in groups],[5,6,7,4])
        self.assertEqual(groups[0].subtitle,'Tuesday, September 22 · Oakland and Venice')
        self.assertEqual(outer_corners(0,5,6),(True,False,False,False))
        self.assertEqual(outer_corners(4,5,6),(False,True,True,False))
        self.assertEqual(outer_corners(5,5,6),(False,False,True,True))
        self.assertEqual(outer_corners(1,5,2),(False,True,True,False))
        columns,side,height=tile_layout(920,168,6)
        self.assertEqual(columns,5)
        self.assertAlmostEqual(side,181.6)
        self.assertAlmostEqual(height,366.2)

    def test_query_snapshot_does_not_follow_later_ui_changes(self):
        snapshot=self.state.query_snapshot()
        self.state.view='fav'; self.state.query='Lisbon'
        self.assertEqual(self.state.read_page(snapshot=snapshot).total,22)
        self.assertEqual(self.state.read_page().total,1)

    def test_pager_crosses_catalog_pages_and_stops_at_collection_ends(self):
        self.state.page_offset=200; self.state.total=222
        self.state.open_photo('0')
        self.assertEqual(self.state.step_destination(-1),(0,199))
        self.state.open_photo('21')
        self.assertIsNone(self.state.step_destination(1))
        self.state.page_offset=0; self.state.total=22; self.state.open_photo('0')
        self.assertIsNone(self.state.step_destination(-1))

    def test_information_follows_steps_and_closes_with_subject(self):
        self.state.open_photo('0'); self.state.info=True
        self.assertFalse(self.state.step(-1))
        self.assertTrue(self.state.step(1)); self.assertTrue(self.state.info)
        self.assertEqual(self.state.viewer,'1')
        self.state.switch_view('fav'); self.state.apply_page(self.state.read_page())
        self.assertIsNone(self.state.viewer); self.assertFalse(self.state.info)

    def test_cancel_discards_auto_and_save_preserves_unedited_values(self):
        self.source.update_adjustments('0',{'w':8,'e':12})
        self.state.open_photo('0'); self.state.load_adjustments('0'); self.state.begin_edit()
        self.state.auto_adjust(); self.state.cancel_edit()
        self.assertEqual(self.source.adjustments('0'),{'w':8,'e':12})
        self.state.begin_edit(); self.state.draft['e']=20
        self.source.update_adjustments('0',{'w':15})
        self.state.save_edit('0',self.state.baseline,self.state.draft)
        self.assertEqual(self.source.adjustments('0'),{'w':15,'e':20})

    def test_favourite_and_delete_undo_do_not_write_fixture_files(self):
        self.state.set_favourite('0',False)
        self.assertFalse(self.state.records[0].favorite)
        self.state.trash('0')
        self.assertEqual(len(self.source.rows()),21)
        self.assertEqual(self.state.undo_trash(),'0')
        self.assertEqual(len(self.source.rows()),22)
        self.assertTrue(FixtureSource(ROOT/'tests/fixtures/photos-v70.json').asset('0').favorite)

    def test_partial_delete_keeps_undo_for_the_successful_copies(self):
        from prairie_apps.photos_backend import TransferResult
        original=self.source.trash_copies
        def partial(ids):
            result=original(ids)
            return TransferResult(result.completed,errors=('Disconnected copy',))
        self.source.trash_copies=partial
        result=self.state.trash('0')
        self.assertEqual(result.errors,('Disconnected copy',))
        self.assertEqual(self.state.undo_trash(),'0')
        self.assertEqual(len(self.source.rows()),22)

    def test_failed_restore_keeps_a_retryable_undo(self):
        from prairie_apps.photos_backend import TransferResult
        self.state.trash('0')
        original=self.source.restore_copies
        self.source.restore_copies=lambda ids:TransferResult((),errors=('Source is disconnected',))
        with self.assertRaisesRegex(OSError,'disconnected'):self.state.undo_trash()
        self.source.restore_copies=original
        self.assertEqual(self.state.undo_trash(),'0')
        self.assertEqual(len(self.source.rows()),22)

    def test_revert_can_be_cancelled_and_removes_only_photo_adjustments(self):
        self.source.update_adjustments('0',{'e':12,'rot':90,'pre':'mono'})
        self.state.open_photo('0'); self.state.load_adjustments('0'); self.state.begin_edit()
        self.state.revert_edit(); self.state.cancel_edit()
        self.assertEqual(self.source.adjustments('0'),{'e':12,'rot':90,'pre':'mono'})
        self.state.begin_edit(); self.state.revert_edit()
        self.state.save_edit('0',self.state.baseline,self.state.draft)
        self.assertEqual(self.source.adjustments('0'),{})
        self.state.cancel_edit()
        with self.assertRaises(ValueError):self.state.revert_edit()

    def test_months_and_all_do_not_invent_groups(self):
        months=groups_for(self.state.records,'months',self.state.now,self.source)
        self.assertEqual([(g.title,len(g.records)) for g in months],[('September',18),('August',4)])
        self.assertEqual(len(groups_for(self.state.records,'all',self.state.now,self.source)),1)
        self.assertEqual(groups_for((),'days',self.state.now,self.source),())

if __name__=='__main__':
    unittest.main()
