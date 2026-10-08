#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise Notes drop targets against real GTK rows and an isolated store."""
import shutil
import unittest
import notes_picture_sizing_runtime as fixture
from prairie_apps.notes import FolderRow, RootDropBoundary
from gi.repository import Gdk

class SidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.PictureSizingTests.setUpClass()
        cls.window = fixture.PictureSizingTests.window
    @classmethod
    def tearDownClass(cls):
        fixture.PictureSizingTests.tearDownClass()
        shutil.rmtree(fixture._home)
    def setUp(self):
        self.w = self.window
        self.a, self.b, self.c = [self.w.store.create_note(title=t) for t in ('One', 'Two', 'Three')]
        self.w._reload_sidebar()
        fixture.pump(.1)
    def test_edge_and_center_have_one_distinct_hint(self):
        w = self.w
        w._drag_value = f'note:{self.c.id}'
        row = w.note_rows[self.a.id]
        for fraction, expected in ((.1, 'before'), (.5, 'into'), (.9, 'after')):
            self.assertEqual(w._sidebar_drag_motion(row, 10, row.get_height() * fraction), Gdk.DragAction.MOVE)
            self.assertEqual([c for c in row.get_css_classes() if c.startswith('drop-')], ['drop-' + expected])
        w._clear_drop_hint()
        self.assertFalse(any(c.startswith('drop-') for c in row.get_css_classes()))
    def test_drop_below_moves_after_not_before(self):
        row = self.w.note_rows[self.c.id]
        self.assertTrue(self.w._sidebar_drop(row, f'note:{self.a.id}', 10, row.get_height() - 1))
        ids = [n.id for n in self.w.store.list_notes()]
        self.assertEqual(ids.index(self.a.id), ids.index(self.c.id) + 1)
    def test_center_nests_and_edge_unnests(self):
        row = self.w.note_rows[self.a.id]
        self.assertTrue(self.w._sidebar_drop(row, f'note:{self.b.id}', 10, row.get_height() / 2))
        self.assertEqual(self.w.store.parent_of(self.b.id), self.a.id)
        self.assertGreater(self.w.note_rows[self.b.id].get_child().get_margin_start(), 0)
        fixture.pump(.1)
        row = self.w.note_rows[self.c.id]
        self.assertTrue(self.w._sidebar_drop(row, f'note:{self.b.id}', 10, 0))
        self.assertIsNone(self.w.store.parent_of(self.b.id))
    def test_cycle_is_rejected_before_showing_hint(self):
        self.w.store.move_note(self.b.id, None, parent_id=self.a.id)
        self.w._reload_sidebar()
        fixture.pump(.1)
        self.w._drag_value = f'note:{self.a.id}'
        row = self.w.note_rows[self.b.id]
        self.assertEqual(self.w._sidebar_drag_motion(row, 10, row.get_height()/2), Gdk.DragAction(0))
        self.assertFalse(self.w._sidebar_drop(row, self.w._drag_value, 10, row.get_height()/2))
    def test_folder_lower_half_reorders_and_never_shows_nesting(self):
        first, second = [self.w.store.create_folder(t) for t in ('First', 'Second')]
        self.w._reload_sidebar()
        fixture.pump(.1)
        row = self.w.folder_rows[second.id]
        self.w._drag_value = f'folder:{first.id}'
        self.w._sidebar_drag_motion(row, 10, row.get_height() - 1)
        self.assertNotIn('drop-into', row.get_css_classes())
        self.assertTrue(self.w._sidebar_drop(row, self.w._drag_value, 10, row.get_height() - 1))
        self.assertGreater(self.w.folder_rows[first.id].get_index(), self.w.folder_rows[second.id].get_index())
    def test_folder_can_drop_between_two_root_notes(self):
        folder = self.w.store.create_folder('Between pages')
        self.w._reload_sidebar()
        fixture.pump(.1)
        row = self.w.note_rows[self.b.id]
        self.assertTrue(self.w._sidebar_drop(row, f'folder:{folder.id}', 40, 0))
        self.assertEqual(self.w.folder_children[folder.id][-1].get_index() + 1,
                         self.w.note_rows[self.b.id].get_index())
    def test_folder_footer_drops_outside_before_next_root_item(self):
        folder = self.w.store.create_folder('Expanded')
        self.w.store.move_note(self.a.id, folder.id)
        self.w.store.place_root('folder', folder.id, 'note:' + self.b.id)
        self.w._reload_sidebar()
        fixture.pump(.1)
        boundary = self.w.folder_children[folder.id][-1]
        self.assertIsInstance(boundary, RootDropBoundary)
        self.assertTrue(self.w._sidebar_drop(boundary, f'note:{self.c.id}', 40, 3))
        self.assertIsNone(self.w.store.get_note(self.c.id).folder_id)
        roots = [n.id for n in self.w.store.root_items()]
        self.assertEqual(roots[roots.index(folder.id) + 1:roots.index(folder.id) + 3], [self.c.id, self.b.id])
    def test_bottom_target_accepts_both_notes_and_folders(self):
        folder = self.w.store.create_folder('Last expanded')
        self.w.store.move_note(self.a.id, folder.id)
        self.w._reload_sidebar()
        fixture.pump(.1)
        self.assertTrue(self.w._sidebar_drop(self.w.root_end, f'note:{self.a.id}', 40, 1))
        self.assertEqual(self.w.store.root_items()[-1].id, self.a.id)
        self.assertIsNone(self.w.store.get_note(self.a.id).folder_id)
        self.assertTrue(self.w._sidebar_drop(self.w.root_end, f'folder:{folder.id}', 40, 1))
        self.assertEqual(self.w.store.root_items()[-1].id, folder.id)
    def test_invalid_row_drop_does_not_bubble_to_root(self):
        row = self.w.note_rows[self.a.id]
        ok, bounds = row.compute_bounds(self.w.sidebar_list)
        self.assertTrue(ok)
        self.assertFalse(self.w._drop_to_root(None, f'note:{self.a.id}', 10, bounds.get_y() + 1))

if __name__ == '__main__':
    unittest.main(verbosity=2)
