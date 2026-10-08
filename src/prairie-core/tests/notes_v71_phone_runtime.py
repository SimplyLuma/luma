# SPDX-License-Identifier: Apache-2.0
"""Notes at phone width (v71): list first, the title island, the writing bar; and back to desktop."""
import os
from pathlib import Path
import time
import unittest
from unittest import mock

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib, Gtk
from prairie_apps.notes import NotesApplication
from prairie_apps.notes_lumaui import NotesLumaWindow, _children

ROOT = Path(__file__).resolve().parents[3]


def pump(seconds=.3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def bar_names(center):
    return [c.get_name() for c in _children(center.bar_row) if getattr(c, 'bar_item', None) is not None]


class PhoneTests(unittest.TestCase):
    width, height = 402, 874

    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {
            'LUMA_NOTES_FIXTURE': str(ROOT / 'tests/fixtures/notes-v70.json'),
            'LUMA_NOTES_STYLE_PATH': str(ROOT / 'src/prairie-core/style/notes.css'),
            'LUMA_NOTES_LIVE_SETTLED': '1',
        })
        cls.env.start()
        cls.app = NotesApplication()
        cls.app.set_application_id('org.projectluma.Notes.Runtime.' + cls.__name__)
        cls.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    @classmethod
    def tearDownClass(cls):
        cls.app.run_dispose()
        cls.env.stop()

    def setUp(self):
        with mock.patch.object(NotesLumaWindow, '_watch_store', side_effect=AssertionError('real store watcher')):
            self.window = NotesLumaWindow(self.app)
        self.window.set_default_size(self.width, self.height)
        self.window.present()
        pump(.8)

    def tearDown(self):
        self.window._flush_save()
        self.window.destroy()
        pump()

    def test_opens_on_the_list(self):
        w = self.window
        self.assertTrue(w.phone)
        self.assertTrue(w.phone_list_host.get_visible())
        self.assertEqual(w.layout.showing, 'list')
        self.assertFalse(w.sidebar.get_visible())
        names = [c.get_name() for c in _children(w.phone_cards)]
        self.assertEqual(names[1:3], ['nt-card-1', 'nt-card-2'], 'Pinned first: Launch checklist, Groceries')
        self.assertIn('nt-list-search', bar_names(w.phone_list_bar))
        self.assertIn('nt-list-new', bar_names(w.phone_list_bar))
        self.assertFalse(w.phone_island.get_visible())
        self.assertIsNone(w.sidebar_toggle._drawer, 'the desktop tree is not a phone drawer')

    def test_open_pushes_the_note_with_its_island_and_writing_bar(self):
        w = self.window
        w._phone_open(w.store.get_note('1'))
        pump()
        self.assertEqual(w.layout.showing, 'detail')
        self.assertTrue(w.phone_island.get_visible())
        self.assertEqual(w.phone_island.title, 'Launch')
        self.assertEqual(w.phone_island.subtitle, 'Priya is here · Edited just now')
        self.assertTrue(w.phone_island.presence_dot.get_visible())
        self.assertFalse(w.meta.get_visible())
        self.assertFalse(w.corner.get_visible())
        self.assertEqual(bar_names(w.format_toolbar),
                         ['nt-tool-formatting', 'nt-tool-checklist', 'nt-tool-add-a-photo', 'nt-tool-new-note'])
        self.assertTrue(w.phone_island.grow_into())
        pump()
        w.phone_island.fold()
        w._phone_back()
        pump()
        self.assertEqual(w.layout.showing, 'list')
        self.assertFalse(w.phone_island.get_visible())

    def test_move_picker_back_returns_to_note_actions(self):
        if self.width >= 560:
            self.skipTest('Phone action panel')
        w = self.window
        w._phone_open(w.store.get_note('1')); pump()
        panel = w._move_menu(); pump()
        back = panel.get_first_child()
        self.assertIsInstance(back, Gtk.Button)
        back.emit('clicked'); pump()
        self.assertEqual(w.format_toolbar._panel_key, 'more')

    def test_search_opens_the_row_and_filters_notes(self):
        w = self.window
        center = w.phone_list_bar
        well = next(c for c in _children(center.bar_row) if c.get_name() == 'nt-list-search')
        search = well.bar_item
        self.assertTrue(search.opens, 'Search is a well that opens the full bar field')
        self.assertFalse(search.entry.get_visible())
        center.open_search(search)
        pump()
        self.assertTrue(center.searching)
        search.set_text('Groceries')
        w._phone_search(search.text)
        pump()
        names = [c.get_name() for c in _children(w.phone_cards) if c.get_name().startswith('nt-card-')]
        self.assertEqual(names, ['nt-card-2'])
        center.close_search()
        pump()
        self.assertFalse(center.searching)
        self.assertIn('nt-list-new', bar_names(center))

    def test_aa_grows_the_bar_and_keeps_it_while_formatting(self):
        w = self.window
        w._phone_open(w.store.get_note('1'))
        pump()
        item = next(i for i in w.phone_tool_items if getattr(i, 'key', None) == 'format')
        w.format_toolbar.grow('format', item.panel)
        pump()
        self.assertEqual(w.format_toolbar.grown, 'format')
        w.buffer.select_range(w.buffer.get_iter_at_offset(0), w.buffer.get_iter_at_offset(10))
        w._tool('bold')
        pump()
        self.assertEqual(w.format_toolbar.grown, 'format')

    def test_folder_dropdown_filters_the_list(self):
        w = self.window
        w._phone_folders()
        pump()
        self.assertEqual(w.phone_list_bar.grown, 'folders')
        w._phone_pick_folder('personal')
        pump()
        names = [c.get_name() for c in _children(w.phone_cards) if c.get_name().startswith('nt-card-')]
        self.assertNotIn('nt-card-1', names)
        self.assertIn('nt-card-2', names)

    def test_new_note_pushes_it(self):
        w = self.window
        w.create_note()
        pump()
        self.assertEqual(w.phone_page, 'note')
        self.assertEqual(w.layout.showing, 'detail')

    def test_crossing_to_desktop_redraws(self):
        w = self.window
        w._phone_open(w.store.get_note('1'))
        pump()
        w.set_default_size(1180, 740)
        pump(1.2)
        self.assertFalse(w.phone)
        self.assertTrue(w.sidebar.get_visible())
        self.assertTrue(w.document_host.get_visible())
        self.assertFalse(w.phone_list_host.get_visible())
        self.assertFalse(w.phone_island.get_visible())
        self.assertTrue(w.meta.get_visible())
        self.assertIn('nt-tool-text', bar_names(w.format_toolbar))


class DesktopTests(PhoneTests):
    width, height = 1180, 740

    def test_desktop_shape(self):
        w = self.window
        self.assertFalse(w.phone)
        self.assertTrue(w.sidebar.get_visible())
        self.assertFalse(w.sidebar_toggle.get_visible())
        self.assertIn('nt-tool-text', bar_names(w.format_toolbar))
        self.assertFalse(w.phone_island.get_visible())
        w.sidebar_toggle.toggle()
        pump()
        self.assertFalse(w.sidebar.get_visible())
        w.sidebar_toggle.toggle()
        pump()
        self.assertTrue(w.sidebar.get_visible())


class CompactTests(PhoneTests):
    width, height = 700, 740

    def test_compact_folds_the_sidebar_at_720(self):
        w = self.window
        self.assertFalse(w.phone)
        self.assertFalse(w.sidebar.get_visible())
        self.assertIsNone(w.sidebar_toggle._drawer)
        w.sidebar_toggle.toggle()
        pump()
        self.assertTrue(w.sidebar.get_visible(), 'F9 brings it back')


for name in [n for n in dir(PhoneTests) if n.startswith('test_')]:
    setattr(DesktopTests, name, None)
    setattr(CompactTests, name, None)


if __name__ == '__main__':
    unittest.main()
