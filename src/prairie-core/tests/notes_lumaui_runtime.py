# SPDX-License-Identifier: Apache-2.0
"""Exercise document edits under a private headless display (the v71 phone: notes_v71_phone_runtime.py)."""
import os
from pathlib import Path
import time
import unittest
from unittest import mock

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gdk, Gio, GLib, Gtk
from prairie_apps.notes import NotesApplication
from prairie_apps.notes_lumaui import NotesLumaWindow, _descendants
import luma_appkit.widgets as widgets

ROOT = Path(__file__).resolve().parents[3]


def pump(seconds=.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class DocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {
            'LUMA_NOTES_FIXTURE': str(ROOT / 'tests/fixtures/notes-v70.json'),
            'LUMA_NOTES_STYLE_PATH': str(ROOT / 'src/prairie-core/style/notes.css'),
        })
        cls.env.start()
        # Production startup must use the published native surface contract.
        # An older typelib is a failed prerequisite, never a mocked getter.
        if not hasattr(widgets.LumaAppearance.SurfacePolicy, 'get_surface'):
            raise AssertionError('Notes runtime requires native SurfacePolicy.get_surface')
        cls.app = NotesApplication()
        # Each fixture class owns separate exports even on the same private bus.
        # GApplication disposal alone does not unregister GTK's D-Bus interface.
        cls.app.set_application_id('org.projectluma.Notes.Runtime.' + cls.__name__)
        cls.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    @classmethod
    def tearDownClass(cls):
        # Each desktop/phone class owns its test application and bus exports.
        # Release them before another class registers on the private bus.
        cls.app.run_dispose()
        cls.env.stop()

    def setUp(self):
        with mock.patch.object(NotesLumaWindow, '_watch_store', side_effect=AssertionError('real store watcher')):
            self.window = NotesLumaWindow(self.app)
        phone = getattr(self, 'phone', False)
        self.window.set_default_size(390 if phone or 'phone_drawer' in self._testMethodName else 1180,
                                     820 if phone else 740)
        self.before_measure = {name: getattr(self.window, name).measure(Gtk.Orientation.HORIZONTAL, -1)[:2]
                               for name in ('layout', 'sidebar', 'document_host', 'canvas', 'title_entry', 'editor', 'body_scroll', 'kids', 'meta', 'title_bar', 'body', 'sheet_layer', 'layer_host')}
        self.before_measure['window'] = self.window.measure(Gtk.Orientation.HORIZONTAL, -1)[:2]
        self.before_measure['default'] = self.window.get_default_size()
        self.window.present()
        pump()

    def tearDown(self):
        self.window._flush_save()
        self.window.destroy()
        pump()

    def test_check_completion_and_inline_formats_survive_save(self):
        w = self.window
        self.assertIn('checked', w.page.block_of_line(1))
        w.page.set_line_block(1, w.page.block_of_line(1) - {'checked'})
        start = w.buffer.get_iter_at_offset(0)
        end = w.buffer.get_iter_at_offset(10)
        w.buffer.select_range(start, end)
        w._tool('strike')
        w._tool('highlight')
        w._flush_save()
        stored = w.store.get_note('1')
        self.assertTrue(any(r['style'] == 'strike' for r in stored.runs))
        self.assertTrue(any(r['style'] == 'highlight' for r in stored.runs))
        w._open_note(stored)
        self.assertNotIn('checked', w.page.block_of_line(1))
        self.assertIn('checked', w.page.block_of_line(2))

    def test_block_style_and_divider_survive_save(self):
        w = self.window
        w.buffer.place_cursor(w.buffer.get_start_iter())
        w._set_block_style('heading')
        w._flush_save()
        self.assertTrue(any(r['style'] == 'heading' and r['start'] == 0 for r in w.store.get_note('1').runs))
        w.buffer.place_cursor(w.buffer.get_end_iter())
        menu = w._insert_menu()
        pump()
        menu.buttons[2].emit('clicked')
        pump()
        w._flush_save()
        self.assertTrue(any(r['style'] == 'divider' for r in w.store.get_note('1').runs))

    def test_insert_image_keeps_blank_paragraph_and_existing_text(self):
        w = self.window
        original = w.current.body
        w.buffer.place_cursor(w.buffer.get_start_iter())
        w._choose_picture_file()
        w._flush_save()
        note = w.store.get_note('1')
        self.assertEqual(note.body, '\ufffc\n\n' + original)
        w._open_note(note)
        self.assertEqual(w.page.serialize()[0], note.body)

    def test_picture_can_resize_repeatedly_without_opening_its_menu(self):
        w = self.window
        w._open_note(w.store.get_note('4'))
        picture = w.page.pictures[0]
        w.page.choose_picture(picture, show_bar=False)
        pump()
        initial = picture.get_width()
        self.assertGreater(initial, 120)
        w.page.picture_commands = mock.Mock(side_effect=AssertionError('resize opened picture menu'))
        gesture = mock.Mock()
        for offset in (-40, -30):
            before = picture.get_width()
            picture._resize_begins(gesture, 0, 0)
            picture._resize_moves(gesture, offset, 0)
            picture._resize_ends(gesture, offset, 0)
            pump()
            self.assertLess(picture.get_width(), before)
        picture._released(mock.Mock(get_current_button=lambda: Gdk.BUTTON_PRIMARY), 1, 0, 0)
        self.assertIsNone(w.picture_bar)
        w.page.picture_commands.assert_not_called()

    def test_new_note_inside_preserves_parent_document(self):
        w = self.window
        original = w.store.get_note('1')
        w._new_inside()
        self.assertEqual(w.store.parent_of(w.current.id), '1')
        self.assertEqual(w.current.folder_id, 'launch')
        self.assertEqual(w.store.get_note('1'), original)

    def test_search_changes_only_the_navigation(self):
        w = self.window
        original = w.store.get_note('1')
        w.search.set_text('sourdough')
        self.assertEqual(set(w.note_rows), {'2', '4'})
        self.assertEqual(w.current.id, '1')
        w.search.set_text('zzzz')
        self.assertEqual(w.note_rows, {})
        self.assertEqual(w.store.get_note('1'), original)

    def test_new_page_clears_search_and_is_visible_at_top_level(self):
        w = self.window
        original = w.store.get_note('1')
        w.search.set_text('zzzz')
        w.create_note()
        self.assertEqual(w.search.get_text(), '')
        self.assertIn(w.current.id, w.note_rows)
        self.assertIsNone(w.current.folder_id)
        self.assertIsNone(w.store.parent_of(w.current.id))
        self.assertEqual(w.store.get_note('1'), original)

    def test_folder_form_callback_validates_before_creating(self):
        w = self.window
        count = len(w.store.list_folders())
        with self.assertRaises(KeyError):
            w._create_folder_from_values('Missing parent', 150, 'missing')
        self.assertEqual(len(w.store.list_folders()), count)
        folder = w._create_folder_from_values('  New folder  ', 150, 'launch')
        self.assertEqual(folder.name, 'New folder')
        self.assertEqual(w.store.folder_parents()[folder.id], 'launch')
        self.assertEqual(w.store.get_mark('folder', folder.id), {'kind': 'dot', 'hue': 150})

    def test_sharing_fixture_changes_are_disposable(self):
        w = self.window
        w._share(w.corner.controls['share'])
        pump()
        self.assertEqual(w.share_sheet.choices, ('work-together', 'send-copy'))
        state = w.fixture_sharing['1']
        person = w._fixture_people()['AC']
        from luma_appkit import Collaborator
        collaborator = Collaborator(person, 'view')
        w._fixture_share_choice(state, 'invite', collaborator)
        self.assertEqual(state['collaborators'][-1].person.username, 'ada')
        w.share_sheet.close()
        self.assertEqual(len(w.store.list_notes()), 11)

    def test_duplicate_nested_note_keeps_its_parent_and_content(self):
        w = self.window
        source = w.store.get_note('8')
        w._duplicate_specific(source)
        self.assertNotEqual(w.current.id, source.id)
        self.assertEqual(w.store.parent_of(w.current.id), '1')
        self.assertEqual(w.current.folder_id, source.folder_id)
        self.assertEqual(w.current.body, source.body)
        self.assertEqual(w.current.runs, source.runs)

    def test_new_note_can_group_another_note(self):
        w = self.window
        folders = w.store.list_folders()
        count = len(w.store.list_notes())
        add = next(widget for widget in _descendants(w) if widget.get_name() == 'nt-new')
        add.emit('clicked')
        self.assertEqual(len(w.store.list_notes()), count + 1)
        group = w.current
        self.assertIsNone(group.folder_id)
        self.assertEqual(w.store.list_folders(), folders)
        w._new_inside()
        self.assertEqual(w.store.parent_of(w.current.id), group.id)
        self.assertIsNone(w.current.folder_id)

    def test_blank_sidebar_drop_detaches_nested_note_and_folder(self):
        w = self.window
        source = w.store.get_note('8')
        w._open_note(source)
        self.assertTrue(w._blank_root_drop(None, 'note:8', 0, 10000))
        self.assertIsNone(w.store.parent_of('8'))
        self.assertIsNone(w.current.folder_id)
        self.assertEqual(w.current.body, source.body)
        self.assertTrue(w._blank_root_drop(None, 'folder:press', 0, 10000))
        self.assertNotIn('press', w.store.folder_parents())

    def test_blank_sidebar_drop_rejects_unknown_items_without_changes(self):
        w = self.window
        before = w.store.folder_parents()
        self.assertFalse(w._blank_root_drop(None, 'folder:missing', 0, 10000))
        self.assertFalse(w._blank_root_drop(None, 'unsupported:launch', 0, 10000))
        self.assertEqual(w.store.folder_parents(), before)

    def test_fixture_image_insertion_uses_only_the_readonly_sample(self):
        w = self.window
        from prairie_apps.notes_richtext import RichTextPage
        with mock.patch.object(RichTextPage, 'import_files', side_effect=AssertionError('disk import')):
            w._choose_picture_file()
        w._flush_save()
        run = next(r for r in w.current.runs if r['style'] == 'image')
        self.assertEqual(w.store.picture_paths[run['src']].name, 'life-sync-terrace.webp')
        self.assertTrue(w.page.pictures[0].texture)

    def test_file_card_survives_save_reload_and_removal(self):
        w = self.window
        metadata = {'src': 'a' * 64 + '.pdf', 'name': 'Brief.pdf', 'size': 21}
        w.page.insert_file(metadata)
        w._flush_save()
        stored = w.store.get_note('1')
        self.assertEqual(next(r['name'] for r in stored.runs if r['style'] == 'file'), 'Brief.pdf')
        w._open_note(stored)
        anchor, run = w.page.files[0]
        self.assertEqual(run['src'], metadata['src'])
        found = w.buffer.get_iter_at_child_anchor(anchor)
        after = found.copy()
        after.forward_char()
        w.buffer.delete(found, after)
        w._flush_save()
        self.assertFalse(any(r['style'] == 'file' for r in w.store.get_note('1').runs))

    def test_async_file_completion_keeps_original_note_when_selection_changes(self):
        w = self.window
        w._open_note(w.store.get_note('2'))
        before = w.store.get_note('2')
        metadata = {'src': 'a' * 64 + '.pdf', 'name': 'Brief.pdf', 'size': 21}
        w._note_file_ready('1', metadata, None)
        self.assertEqual(w.store.get_note('2'), before)
        self.assertTrue(any(r['style'] == 'file' for r in w.store.get_note('1').runs))

    def test_save_failure_is_visible_and_retries_without_losing_edits(self):
        w = self.window
        before = w.store.get_note('1')
        from prairie_apps.notes_lumaui import Toast
        with mock.patch.object(w.store, 'update_note', side_effect=OSError('disk full')), \
                mock.patch.object(Toast, 'show') as show:
            w.title_entry.set_text('Still in the editor')
            w._flush_save()
            self.assertEqual(w.store.get_note('1'), before)
            self.assertEqual(w.title_entry.get_text(), 'Still in the editor')
            self.assertTrue(w.save_source)
            self.assertEqual(show.call_args.kwargs['kind'], 'error')
            self.assertIn('disk full', show.call_args.args[1])
        w._flush_save()
        self.assertEqual(w.store.get_note('1').title, 'Still in the editor')

    def test_plain_text_changes_only_selected_inline_formats(self):
        w = self.window
        w.buffer.select_range(w.buffer.get_iter_at_offset(0), w.buffer.get_iter_at_offset(10))
        w._tool('bold')
        w._flush_save()
        before = w.store.get_note('1')
        w.buffer.place_cursor(w.buffer.get_iter_at_offset(1))
        w._plain_text()
        w._flush_save()
        self.assertEqual(w.store.get_note('1'), before)
        w.buffer.select_range(w.buffer.get_iter_at_offset(0), w.buffer.get_iter_at_offset(4))
        w._plain_text()
        w._flush_save()
        after = w.store.get_note('1')
        self.assertEqual(after.body, before.body)
        self.assertEqual([r for r in after.runs if r['style'] == 'heading'],
                         [r for r in before.runs if r['style'] == 'heading'])
        self.assertEqual([r for r in after.runs if r['style'] == 'checked'],
                         [r for r in before.runs if r['style'] == 'checked'])
        self.assertEqual(next(r for r in after.runs if r['style'] == 'bold')['start'], 4)


if __name__ == '__main__':
    unittest.main()
