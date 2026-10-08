# SPDX-License-Identifier: Apache-2.0
"""Strict v70 action/state assertions, on desktop and in a narrow (v71 compact, 760px) window.

v71 gave the phone (under 560) its own shape: notes_v71_phone_runtime.py covers it.

Run with notes_lumaui_headless.py after the capsule submission pause ends.
Missing shared contracts FAIL; no skipped cases or expected failures.
All edits use the in-memory fixture and its read-only assets.
"""
import inspect
import unittest
from unittest import mock
from gi.repository import Gdk, Gio, Gtk
import notes_lumaui_runtime as base
from prairie_apps.notes_lumaui import _descendants
from luma_appkit import MarkPicker, MarkValue
from luma_appkit.rows_menu import RichMenuItem


#: v71 compact, above the 720 fold: the v70 surfaces these assertions name (sidebar, corner, toolbar).
NARROW = 760


class SurfaceAssertions:
    phone = False
    setUpClass = classmethod(base.DocumentTests.setUpClass.__func__)
    tearDownClass = classmethod(base.DocumentTests.tearDownClass.__func__)
    tearDown = base.DocumentTests.tearDown

    def setUp(self):
        base.DocumentTests.setUp(self)
        if self.phone:
            self.window.set_default_size(NARROW, 820)
            base.pump(.4)
            self.assertEqual((self.window.get_width(), self.window.get_height()), (NARROW, 820))

    def named(self, name):
        matches = [w for w in _descendants(self.window) if w.get_name() == name and w.get_mapped()]
        self.assertTrue(matches, 'Required mapped control missing: ' + name)
        return matches[0]

    def click(self, name):
        widget = self.named(name)
        if isinstance(widget, Gtk.ToggleButton):
            widget.set_active(not widget.get_active())
        else:
            widget.emit('clicked')
        base.pump(.06)
        # Phone modal removal follows the shared dialog fade. Keep the actual
        # mapped-state assertion, but allow the asynchronous close to finish.
        sheet = getattr(self.window, 'share_sheet', None)
        if sheet is not None and sheet.floater is not None and not sheet.floater.is_open:
            deadline = base.time.monotonic() + 2
            while sheet.get_mapped() and base.time.monotonic() < deadline:
                base.pump(.02)
        return widget

    def select(self):
        w = self.window
        w.editor.grab_focus()
        # GTK completes its first focus transition asynchronously. Select only
        # after that transition, as a user does, so it cannot collapse the range.
        base.pump(.1)
        self.assertIs(w.get_focus(), w.editor)
        w.buffer.select_range(w.buffer.get_iter_at_offset(0), w.buffer.get_iter_at_offset(10))
        base.pump(.35)
        self.assertTrue(w.buffer.get_has_selection())
        self.assertTrue(w.selection_bubble.get_mapped(), 'Actual selected-text bubble must be visible')

    def sidebar(self):
        if self.phone and not self.window.sidebar.get_mapped():
            self.window.sidebar_toggle.toggle()  # F9
            base.pump(.15)
        self.assertTrue(self.window.sidebar.get_mapped())

    def share(self, copy=False):
        self.click('nt-share')
        self.named('nt-share-sheet')
        if copy:
            self.click('nt-share-send-copy')
            self.assertEqual(self.window.share_sheet.mode, 'send-copy')

    def labels(self, widget):
        return [w.get_text() for w in _descendants(widget) if isinstance(w, Gtk.Label)]

    def test_responsive_widths_keep_every_document_tool_inside_window(self):
        w = self.window
        height = 820 if self.phone else 740
        for width in (360, 500, 1024, 1440):
            with self.subTest(width=width):
                w.set_default_size(width, height)
                base.pump(.3)
                self.assertEqual((w.get_width(), w.get_height()), (width, height))
                self.assertTrue(w.editor.get_mapped())
                for name in ('text', 'bold', 'italic', 'strikethrough', 'highlight',
                             'link', 'checklist', 'bulleted-list', 'numbered-list', 'insert'):
                    self.named('nt-tool-' + name)
                # Measure the visible bar. Its content may be wider inside
                # the shared horizontal scroller without losing any tool.
                ok, bounds = w.format_toolbar.bar.compute_bounds(w)
                self.assertTrue(ok)
                self.assertGreaterEqual(bounds.get_x(), -2, 'Tools must fit or scroll inside the window')
                self.assertLessEqual(bounds.get_x() + bounds.get_width(), width + 2,
                                     'All tools remain available at compact widths')

    def test_capture_fixture_selection_survives_initial_focus(self):
        previous = self.window
        with mock.patch.dict(base.os.environ, {'LUMA_NOTES_SELECTION': '0:10'}):
            with mock.patch.object(type(previous), '_watch_store',
                                   side_effect=AssertionError('real store watcher')):
                self.window = type(previous)(self.app)
            self.window.set_default_size(NARROW if self.phone else 1180,
                                         820 if self.phone else 740)
            self.window.present()
            base.pump(.9)
        previous.destroy()
        w = self.window
        self.assertIs(w.get_focus(), w.editor)
        self.assertTrue(w.buffer.get_has_selection())
        start, end = w.buffer.get_selection_bounds()
        self.assertEqual((start.get_offset(), end.get_offset()), (0, 10))
        self.assertTrue(w.selection_bubble.get_mapped())

    def test_selected_text_and_all_format_actions(self):
        w = self.window
        for key, style in [('underline', 'underline'), ('strike', 'strike'),
                           ('bulleted', 'bulleted'), ('numbered', 'numbered')]:
            with self.subTest(format=key):
                w._open_note(w.store.get_note('1'))
                self.select()
                self.click('nt-selection-more')
                for required in ('underline', 'strike', 'bulleted', 'numbered', 'plain'):
                    self.named('nt-format-' + required)
                self.click('nt-format-' + key)
                w._flush_save()
                self.assertTrue(any(r['style'] == style and r['start'] == 0
                                    for r in w.store.get_note('1').runs), style)
        self.select()
        self.click('nt-selection-bold')
        self.click('nt-selection-more')
        self.click('nt-format-plain')
        w._flush_save()
        self.assertFalse(any(r['style'] in ('bold', 'underline', 'strike') and r['start'] < 10
                             for r in w.store.get_note('1').runs))

    def test_selection_inline_actions_preserve_text_and_save_runs(self):
        w = self.window
        before = w.current.body
        for style in ('bold', 'italic', 'highlight', 'link'):
            with self.subTest(style=style):
                self.select()
                self.click('nt-selection-' + style)
                w._flush_save()
                self.assertEqual(w.store.get_note('1').body, before)
                matches = [r for r in w.store.get_note('1').runs if r['style'] == style]
                self.assertTrue(matches, style)
                self.assertEqual((matches[0]['start'], matches[0]['end']), (0, 10))
                if style == 'link':
                    self.assertEqual(matches[0]['href'], 'https://simplyluma.com')

    def test_share_save_and_print_feedback_close_sheet_without_io(self):
        w = self.window
        for action in ('save', 'print'):
            with self.subTest(action=action):
                self.share(copy=True)
                with mock.patch.object(w, '_export_note', side_effect=AssertionError('real export')), \
                     mock.patch.object(w, '_print_note', side_effect=AssertionError('real printer')), \
                     mock.patch('prairie_apps.notes_lumaui.Toast.show') as toast:
                    self.click('nt-share-action-' + action)
                    self.assertTrue(toast.called)
                    self.assertFalse(w.share_sheet.get_mapped())

    def test_style_menu_actions_apply_to_actual_paragraph(self):
        w = self.window
        for style in ('heading', 'quote', 'text'):
            with self.subTest(style=style):
                w.editor.grab_focus()
                w.buffer.place_cursor(w.buffer.get_start_iter())
                self.click('nt-tool-text')
                self.assertEqual(w.style_picker.widget.get_name(), 'nt-tool-text',
                                 'Naming menu Text must preserve the main Text control')
                self.click('nt-style-' + style)
                w._flush_save()
                block = w.page.block_of_line(0)
                self.assertEqual(block & {'heading', 'quote'}, set() if style == 'text' else {style})

    def test_existing_image_context_commands_use_shared_menu(self):
        w = self.window
        w._open_note(w.store.get_note('4'))
        base.pump()
        self.assertTrue(w.page.pictures)
        w._picture_menu(w.page.pictures[0], 10, 10)
        base.pump()
        for command in ('left', 'center', 'right', 'full', 'reset', 'copy', 'replace', 'delete'):
            self.named('nt-command-picture-' + command)
        w.picture_bar.popdown()

    def test_picture_menu_commands_change_only_the_selected_picture(self):
        w = self.window
        w._open_note(w.store.get_note('4'))
        base.pump(.2)
        picture = w.page.pictures[0]
        original_name = picture.name
        original_text = w.page.serialize()[0]
        for command, alignment in (('center', 'align-center'), ('right', 'align-right'), ('left', '')):
            w._picture_menu(picture, 10, 10)
            base.pump(.1)
            self.click('nt-command-picture-' + command)
            line = w.page.picture_line(picture)
            block = w.page.block_of_line(line)
            self.assertEqual(block & {'align-center', 'align-right'},
                             {alignment} if alignment else set())
            self.assertEqual(w.page.serialize()[0], original_text)
        for command in ('full', 'reset'):
            w._picture_menu(picture, 10, 10)
            base.pump(.1)
            self.click('nt-command-picture-' + command)
            self.assertEqual(picture.width, picture.display_size()[0] if command == 'full' else 0)
            self.assertEqual(picture.name, original_name)
            self.assertEqual(w.page.serialize()[0], original_text)
        w._picture_menu(picture, 10, 10)
        base.pump(.1)
        self.click('nt-command-picture-copy')
        clipboard = w.get_clipboard()
        print('Actual picture clipboard formats:', clipboard.get_formats().to_string())
        copied = []
        clipboard.read_texture_async(None, lambda source, result:
                                     copied.append(source.read_texture_finish(result)))
        deadline = base.time.monotonic() + 2
        while not copied and base.time.monotonic() < deadline:
            base.pump(.02)
        self.assertTrue(copied, 'Copy Picture must provide a readable texture')
        self.assertIsInstance(copied[0], Gdk.Texture)
        self.assertEqual((copied[0].get_width(), copied[0].get_height()),
                         (picture.texture.get_width(), picture.texture.get_height()))
        self.assertEqual(w.page.serialize()[0], original_text)
        w._picture_menu(picture, 10, 10)
        base.pump(.1)
        self.click('nt-command-picture-delete')
        w._flush_save()
        self.assertFalse(w.page.pictures)
        self.assertFalse(any(run.get('style') == 'image' for run in w.store.get_note('4').runs))
        self.assertEqual(w.page.serialize()[0].replace('\n', ''),
                         original_text.replace('\ufffc', '').replace('\n', ''))

    def test_picture_menu_cannot_format_a_note_opened_after_it(self):
        w = self.window
        w._open_note(w.store.get_note('4'))
        base.pump(.2)
        w._picture_menu(w.page.pictures[0], 10, 10)
        base.pump(.1)
        stale_action = self.named('nt-command-picture-center')
        original = w.store.get_note('1')
        self.assertTrue(w._open_note(original))
        base.pump(.1)
        stale_action.emit('clicked')
        base.pump(.1)
        w._flush_save()
        self.assertEqual(w.store.get_note('1'), original,
                         'A late picture menu callback must not change the next document')

    def test_late_picture_replacement_does_not_import_after_navigation_or_close(self):
        w = self.window
        for leave in ('stay', 'navigate', 'close'):
            with self.subTest(leave=leave):
                w._open_note(w.store.get_note('4'))
                base.pump(.1)
                picture = w.page.pictures[0]
                source_path = w.store.picture_paths[picture.name]
                dialog = mock.Mock()
                dialog.open_finish.return_value = Gio.File.new_for_path(str(source_path))
                with mock.patch('prairie_apps.notes.Gtk.FileDialog', return_value=dialog), \
                     mock.patch('prairie_apps.notes_attachments.store_picture', return_value=picture.name) as save:
                    # Exercise the real file callback with an inert picker and
                    # read-only fixture asset; never open a chooser or write files.
                    w.fixture = False
                    try:
                        w._replace_picture(picture)
                    finally:
                        w.fixture = True
                    callback = dialog.open.call_args.args[2]
                    original = w.store.get_note('1')
                    if leave == 'navigate':
                        self.assertTrue(w._open_note(original))
                    elif leave == 'close':
                        closed_page = w.page.serialize()
                        w.close()
                    base.pump(.1)
                    callback(dialog, object())
                    if leave == 'stay':
                        self.assertTrue(save.called, 'An active replacement must still import')
                        self.assertIn(picture, w.page.pictures)
                    else:
                        self.assertFalse(save.called,
                                         'A stale replacement must not import an orphan attachment')
                    if leave == 'close':
                        self.assertFalse(w.get_mapped())
                        self.assertEqual(w.page.serialize(), closed_page)
                    else:
                        self.assertEqual(w.store.get_note('1'), original)

    def test_insert_divider_keeps_existing_paragraph_text_and_style(self):
        w = self.window
        body = 'Alpha bravo\nSecond paragraph'
        for offset in (0, 5, 11, len(body)):
            with self.subTest(offset=offset):
                note = w.store.create_note(title='Divider integrity', body=body,
                                          runs=({'start': 0, 'end': 12, 'style': 'quote'},))
                self.assertTrue(w._open_note(note))
                base.pump(.1)
                w.editor.grab_focus()
                base.pump(.1)
                w.buffer.place_cursor(w.buffer.get_iter_at_offset(offset))
                self.click('nt-tool-insert')
                self.click('nt-insert-divider')
                w._flush_save()
                saved = w.store.get_note(note.id)
                self.assertEqual(saved.body.replace('\n', ''), body.replace('\n', ''))
                dividers = [run for run in saved.runs if run['style'] == 'divider']
                self.assertTrue(dividers, 'Actual Insert menu must save a divider')
                if offset in (11, len(body)):
                    self.assertFalse(w.page.block_of_line(w.page.cursor_line()),
                                     'Typing after an end-of-paragraph divider starts plain text')
                for run in dividers:
                    self.assertFalse(saved.body[run['start']:run['end']].strip(),
                                     'A divider must not hide existing paragraph text')
                if offset < 12:
                    quotes = [run for run in saved.runs if run['style'] == 'quote']
                    quoted = ''.join(saved.body[run['start']:run['end']] for run in quotes)
                    self.assertEqual(quoted.replace('\n', ''), 'Alpha bravo')

    def test_failed_save_keeps_draft_when_navigation_or_creation_is_requested(self):
        w = self.window
        count = len(w.store.list_notes())
        for error in (OSError('disk full'), KeyError('missing note row')):
            with self.subTest(error=type(error).__name__):
                original = w.store.get_note('1')
                draft = 'Draft that must survive ' + type(error).__name__
                w.title_entry.set_text(draft)
                with mock.patch.object(w.store, 'update_note', side_effect=error), \
                     mock.patch('prairie_apps.notes_lumaui.Toast.show'):
                    self.assertFalse(w._open_note(w.store.get_note('2')))
                    self.assertEqual(w.current.id, '1')
                    self.assertEqual(w.title_entry.get_text(), draft)
                    w.create_note()
                    w._new_inside()
                    w._duplicate_specific(original)
                    w._new_in_folder(w.store.get_folder('personal'))
                    self.assertEqual(len(w.store.list_notes()), count)
                    w.move_note('1', 'personal', None)
                    w._place_at_root('note', '1', None)
                    w._delete_specific(original)
                    self.assertEqual(w.current.id, '1')
                    self.assertTrue(w._close_notes(w), 'A failed save must block window close')
                    self.assertEqual(w.store.get_note('1'), original)
                    self.assertEqual(w.title_entry.get_text(), draft)
                self.assertTrue(w._can_leave_document(), 'Retry must save the retained draft')
                self.assertEqual(w.store.get_note('1').title, draft)
        original_mark = w.store.get_mark('folder', 'launch')
        with mock.patch.object(w.store, 'set_mark', side_effect=OSError('disk full')), \
             mock.patch('prairie_apps.notes_lumaui.Toast.show') as toast:
            w._save_mark('folder', 'launch', MarkValue('icon', 150, 'star'))
            self.assertTrue(toast.called, 'Mark failure must be visible')
        self.assertEqual(w.store.get_mark('folder', 'launch'), original_mark)

    def test_folder_drop_after_nested_folder_keeps_exact_sibling_position(self):
        self.sidebar()
        w = self.window
        original_notes = w.store.list_notes()
        self.assertTrue(w._tree_drop(w.folder_rows['press'], 'folder:ideas', 'after'))
        base.pump()
        self.assertEqual(w.store.folder_parents()['ideas'], 'launch')
        order = [item.id for item in w.store.root_items() if hasattr(item, 'expanded')]
        self.assertLess(order.index('press'), order.index('ideas'),
                        'After a nested folder must target that folder, not its root ancestor')
        self.assertTrue(w._tree_drop(w.folder_rows['press'], 'folder:ideas', 'before'))
        base.pump()
        order = [item.id for item in w.store.root_items() if hasattr(item, 'expanded')]
        self.assertLess(order.index('ideas'), order.index('press'))
        self.assertEqual(w.store.list_notes(), original_notes)

    def test_failed_folder_drop_preserves_parent_and_order(self):
        self.sidebar()
        w = self.window
        w.store.place_root('folder', 'ideas', None)
        before_parents, before_order = w.store.folder_parents(), w.store.root_items()
        with w.store.connection:
            w.store.connection.execute("CREATE TRIGGER reject_root_order BEFORE INSERT ON metadata "
                "WHEN NEW.key='sidebar-root-order' BEGIN SELECT RAISE(ABORT, 'move write failed'); END")
        with mock.patch('prairie_apps.notes_lumaui.Toast.show'):
            self.assertFalse(w._tree_drop(w.folder_rows['press'], 'folder:ideas', 'after'))
        self.assertEqual(w.store.folder_parents(), before_parents,
                         'A failed position write must not leave the folder reparented')
        self.assertEqual(w.store.root_items(), before_order)

    def test_nested_folder_root_drops_preserve_documents_and_position(self):
        self.sidebar()
        w = self.window
        before_notes = w.store.list_notes()
        anchor = next(item for item in w.store.root_items()
                      if not hasattr(item, 'expanded'))
        for where in ('before', 'after'):
            with self.subTest(where=where):
                self.assertTrue(w._tree_drop(w.note_rows[anchor.id], 'folder:press', where))
                base.pump(.1)
                self.assertNotIn('press', w.store.folder_parents())
                keys = [w.store.root_key(item) for item in w.store.root_items()]
                offset = -1 if where == 'before' else 1
                self.assertEqual(keys.index('folder:press'),
                                 keys.index('note:' + anchor.id) + offset)
                self.assertEqual(w.store.list_notes(), before_notes)
        self.assertTrue(w._tree_drop(w.folder_rows['launch'], 'folder:press', 'inside'))
        base.pump(.1)
        empty_y = w.sidebar_list.get_height() + 100
        self.assertIsNone(w.sidebar_list.get_row_at_y(empty_y))
        self.assertTrue(w._blank_root_drop(None, 'folder:press', 30, empty_y))
        base.pump(.1)
        self.assertNotIn('press', w.store.folder_parents())
        self.assertEqual(w.store.root_key(w.store.root_items()[-1]), 'folder:press')
        self.assertEqual(w.store.list_notes(), before_notes)

    def test_failed_nested_folder_root_drops_keep_parent_and_documents(self):
        self.sidebar()
        w = self.window
        w.store.place_root('folder', 'ideas', None)
        before_parents, before_order = w.store.folder_parents(), w.store.root_items()
        before_notes = w.store.list_notes()
        anchor = next(item for item in before_order if not hasattr(item, 'expanded'))
        with w.store.connection:
            w.store.connection.execute("CREATE TRIGGER reject_root_order BEFORE INSERT ON metadata "
                "WHEN NEW.key='sidebar-root-order' BEGIN SELECT RAISE(ABORT, 'move write failed'); END")
        empty_y = w.sidebar_list.get_height() + 100
        for drop in (lambda: w._tree_drop(w.note_rows[anchor.id], 'folder:press', 'before'),
                     lambda: w._blank_root_drop(None, 'folder:press', 30, empty_y)):
            with mock.patch('prairie_apps.notes_lumaui.Toast.show'):
                self.assertFalse(drop())
            self.assertEqual(w.store.folder_parents(), before_parents)
            self.assertEqual(w.store.root_items(), before_order)
            self.assertEqual(w.store.list_notes(), before_notes)

    def test_folder_root_drop_reports_backup_failure_without_moving(self):
        self.sidebar()
        w = self.window
        before_parents, before_order = w.store.folder_parents(), w.store.root_items()
        anchor = next(item for item in before_order if not hasattr(item, 'expanded'))
        with mock.patch.object(w.store, 'move_folder', side_effect=OSError('backup disk full')), \
             mock.patch('prairie_apps.notes_lumaui.Toast.show') as toast:
            self.assertFalse(w._tree_drop(w.note_rows[anchor.id], 'folder:press', 'before'))
            self.assertTrue(toast.called, 'Backup failure must be visible and must reject the drop')
        self.assertEqual(w.store.folder_parents(), before_parents)
        self.assertEqual(w.store.root_items(), before_order)

    def test_reparented_folder_refreshes_open_note_path(self):
        w = self.window
        w._open_note(w.store.get_note('3'))
        base.pump(.2)
        self.sidebar()
        before = w.store.get_note('3')
        self.assertIn('Launch', ' '.join(self.labels(w.meta)))
        self.assertTrue(w._tree_drop(w.folder_rows['ideas'], 'folder:press', 'inside'))
        base.pump(.2)
        self.assertEqual(w.store.folder_parents()['press'], 'ideas')
        self.assertIn('Ideas', ' '.join(self.labels(w.meta)),
                      'Open note path follows the reparented folder')
        self.assertNotIn('Launch', ' '.join(self.labels(w.meta)))
        self.assertEqual(w.store.get_note('3'), before)

    def test_folder_mark_tabs_and_persisted_choice(self):
        self.sidebar()
        self.click('nt-folder-mark-launch')
        picker = self.named('nt-mark-picker')
        self.assertIsInstance(picker, MarkPicker)
        for tab in ('icon', 'emoji', 'dot'):
            self.click('nt-mark-tab-' + tab)
            self.assertEqual(picker.tab, tab)
        self.click('nt-mark-tab-icon')
        self.assertTrue(picker.grid_buttons)
        picker.grid_buttons[0].emit('clicked')
        base.pump()
        self.assertEqual(self.window.store.get_mark('folder', 'launch')['kind'], 'icon')
        self.click('nt-folder-mark-launch')
        self.click('nt-mark-tab-dot')
        picker = self.named('nt-mark-picker')
        self.click('nt-mark-hue-blue')
        self.assertEqual(self.window._folder_hue('launch'), 220)
        self.assertFalse(picker.get_mapped(), 'Dot colour choice must close the actual picker, as v70 does')
        self.click('nt-folder-mark-launch')
        picker = self.named('nt-mark-picker')
        self.click('nt-mark-hue-grey')
        self.assertEqual(self.window.store.get_mark('folder', 'launch')['hue'], 'grey')
        self.assertEqual(self.window._folder_hue('launch'), 220,
                         'Grey changes the mark while retaining the folder document colour')
        self.assertFalse(picker.get_mapped())
        self.click('nt-folder-mark-launch')
        self.click('nt-mark-tab-emoji')
        self.named('nt-mark-picker').grid_buttons[0].emit('clicked')
        base.pump()
        self.assertEqual(self.window._folder_hue('launch'), 220)

    def test_unset_page_mark_and_note_picker(self):
        w = self.window
        self.assertTrue(w.mark.get_visible(), 'Unset page mark requires published kind=page contract')
        self.click('nt-mark')
        picker = self.named('nt-mark-picker')
        picker.tabs['emoji'].set_active(True)
        picker.grid_buttons[0].emit('clicked')
        base.pump()
        self.assertEqual(w.store.get_mark('note', '1')['kind'], 'emoji')
        self.click('nt-mark')
        picker = self.named('nt-mark-picker')
        untouched = w.store.get_mark('note', '2')
        w._open_note(w.store.get_note('2'))
        picker.tabs['icon'].set_active(True)
        picker.grid_buttons[0].emit('clicked')
        base.pump()
        self.assertEqual(w.current.id, '2')
        self.assertEqual(w.store.get_mark('note', '1')['kind'], 'icon')
        self.assertEqual(w.store.get_mark('note', '2'), untouched,
                         'Page mark belongs to the note that opened its picker')
        self.sidebar()
        self.click('nt-note-mark-1')
        self.named('nt-mark-picker')

    def test_folder_form_cancel_and_atomic_create(self):
        w = self.window
        self.sidebar()
        before = w.store.list_folders()
        self.click('nt-new-folder')
        field = self.named('nt-folder-name')
        self.named('nt-folder-create')
        self.assertEqual(w.store.list_folders(), before, 'Opening form must not write')
        field.set_text('Cancelled')
        w.folder_form.close()
        base.pump()
        self.assertEqual(w.store.list_folders(), before, 'Cancelling form must not write')
        self.click('nt-new-folder')
        self.named('nt-folder-name').set_text('Trips')
        self.click('nt-folder-create')
        made = [f for f in w.store.list_folders() if f.name == 'Trips']
        self.assertEqual(len(made), 1)
        self.assertEqual(w.store.get_mark('folder', made[0].id), {'kind': 'dot', 'hue': 150})
        self.assertIsNone(w.store.folder_parents().get(made[0].id))

    def test_nested_folder_form_preserves_parent(self):
        w = self.window
        self.sidebar()
        w.show_menu(w.folder_commands(w.store.get_folder('launch')), w.folder_rows['launch'], 10, 10)
        base.pump()
        self.click('nt-command-folder-new-inside')
        self.named('nt-folder-name').set_text('Trips')
        self.click('nt-folder-create')
        made = [f for f in w.store.list_folders() if f.name == 'Trips']
        self.assertEqual(len(made), 1)
        self.assertEqual(w.store.folder_parents()[made[0].id], 'launch')

    def test_new_notes_lead_scope_without_rewriting_siblings(self):
        w = self.window
        before = {n.id: n for n in w.store.list_notes()}
        first = w.store.create_note(folder_id='launch', first=True)
        newer = w.store.create_note(folder_id='launch', first=True)
        self.assertLess(newer.sort_order, first.sort_order)
        self.assertLess(first.sort_order, min(n.sort_order for n in before.values()
                                             if n.folder_id == 'launch'))
        child = w.store.create_note(folder_id='launch', parent_id='1', first=True)
        self.assertEqual(w.store.parent_of(child.id), '1')
        self.assertTrue(all(w.store.get_note(key) == value for key, value in before.items()))
        count = len(w.store.list_notes())
        with self.assertRaises(ValueError):
            w.store.create_note(folder_id='personal', parent_id='1', first=True)
        self.assertEqual(len(w.store.list_notes()), count)
        w._new_in_folder(w.store.get_folder('personal'))
        self.assertEqual(w.current.folder_id, 'personal',
                          'New note here uses the chosen folder, independent of the open note')
        w._open_note(w.store.get_note('11'))
        w._new_inside()
        shared_child = w.current
        self.assertEqual(w.store.parent_of(shared_child.id), '11')
        self.assertIsNone(shared_child.folder_id)
        self.sidebar()
        self.named('nt-note-' + shared_child.id)
        self.assertEqual(w.note_rows[shared_child.id].depth, 1,
                         'Shared-with-me roots expose their nested notes, as v70 leaf does')

    def test_move_hierarchy_and_captured_subject(self):
        w = self.window
        self.assertIn('lead', inspect.signature(RichMenuItem.__init__).parameters,
                      'Move requires shared Mark lead contract')
        self.assertIn('depth', inspect.signature(RichMenuItem.__init__).parameters)
        w._open_note(w.store.get_note('8'))
        self.click('nt-more')
        self.click('nt-command-note-move')
        for folder in w.store.list_folders():
            self.named('nt-move-' + folder.id)
        destination = self.named('nt-move-personal')
        # Store changes must apply to the subject captured when opening the menu.
        w.current = w.store.get_note('2')
        before = w.store.get_note('2')
        destination.emit('clicked')
        base.pump()
        self.assertEqual(w.store.get_note('8').folder_id, 'personal')
        self.assertIsNone(w.store.parent_of('8'))
        self.assertEqual(w.store.get_note('2'), before)

    def test_presence_opens_share_and_document_peer_caret_exists(self):
        presence = self.named('nt-presence')
        self.assertIsInstance(presence, Gtk.Button, 'Published presence action is required')
        # Peer carets are snapshot decorations anchored to live text marks.
        self.assertEqual(self.window.page._collaborators['PR'][1], 'Priya')
        self.click('nt-presence')
        self.named('nt-share-sheet')

    def test_folder_path_and_ancestor_are_actual_navigation_actions(self):
        w = self.window
        w._open_note(w.store.get_note('8'))
        base.pump()
        self.click('nt-ancestor-1')
        self.assertEqual(w.current.id, '1')
        self.click('nt-folder-path')
        for folder in w.store.list_folders():
            self.named('nt-move-' + folder.id)

    def test_sharing_role_and_link_menus_apply(self):
        self.share()
        self.click('nt-share-role-0')
        self.named('nt-share-role-0')
        for role in ('edit', 'comment', 'view', 'remove'):
            self.named('nt-share-permission-' + role)
        self.click('nt-share-permission-view')
        self.assertEqual(self.window.fixture_sharing['1']['collaborators'][0].role, 'view')
        self.click('nt-share-link-picker')
        for role in ('off', 'view', 'edit'):
            self.named('nt-share-access-' + role)
        self.click('nt-share-access-view')
        self.assertEqual(self.window.fixture_sharing['1']['link_access'], 'view')

    def test_sharing_suggestion_invites_once_and_empty_query(self):
        self.share()
        sheet = self.window.share_sheet
        sheet.invite.set_text('zzzz')
        base.pump()
        self.assertTrue(sheet.none_label.get_visible())
        self.assertIn('zzzz', sheet.none_label.get_text())
        sheet.invite.set_text('Ada')
        self.window._label_share_controls()
        base.pump()
        self.click('nt-share-suggestion')
        self.assertEqual(sum(c.person.username == 'ada'
                             for c in self.window.fixture_sharing['1']['collaborators']), 1)
        self.assertEqual(sheet.query, '')

    def test_sharing_targets_send_and_copy_feedback(self):
        self.share(copy=True)
        for target in ('priya', 'messages', 'mail', 'nearby', 'notes', 'write', 'ari'):
            self.named('nt-share-target-' + target)
        self.click('nt-share-target-priya')
        self.assertIn('Priya Raman', self.window.share_sheet.sent)
        button = self.click('nt-share-action-copy')
        self.assertIn('Copied', self.labels(button))

    def test_sharing_copy_link_feedback(self):
        self.share()
        button = self.click('nt-share-copy-link')
        self.assertIn('Copied', self.labels(button))

    def test_copy_targets_keep_subject_when_current_note_changes(self):
        w = self.window
        subject = w.store.get_note('1')
        w._open_note(w.store.get_note('2'))
        untouched = w.store.get_note('2')
        before = {n.id for n in w.store.list_notes()}
        w._share_copy_choice(subject, 'target', 'notes')
        created = [n for n in w.store.list_notes() if n.id not in before]
        self.assertEqual(len(created), 1)
        self.assertEqual((created[0].title, created[0].body, created[0].runs),
                         (subject.title + ' copy', subject.body, subject.runs))
        self.assertEqual(w.store.get_note('2'), untouched)
        self.assertEqual(w.store.get_note('1'), subject)
        with mock.patch.object(w, '_open_in_write') as write:
            w._share_copy_choice(subject, 'target', 'write')
            self.assertEqual(write.call_args.args[0].id, '1')

    def test_each_sharing_app_target_dispatches_and_closes(self):
        w = self.window
        for target in ('messages', 'mail', 'notes', 'write', 'ari'):
            with self.subTest(target=target):
                self.share(copy=True)
                with mock.patch.object(w, '_fixture_share_choice', wraps=w._fixture_share_choice) as choice:
                    self.click('nt-share-target-' + target)
                    self.assertTrue(any(call.args[1:] == ('target', target) for call in choice.call_args_list))
                self.assertFalse(w.share_sheet.get_mapped(), 'Target must close the actual sheet')

    def test_nearby_receiver_list_and_sent_state(self):
        self.share(copy=True)
        self.click('nt-share-target-nearby')
        base.pump(1.5)
        self.named('nt-share-nearby-priyas-thinkpad')
        button = self.click('nt-share-nearby-nicks-phone')
        self.assertIn('Sent', self.labels(button))


class DesktopStates(SurfaceAssertions, unittest.TestCase):
    pass


class PhoneStates(SurfaceAssertions, unittest.TestCase):
    phone = True


if __name__ == '__main__':
    unittest.main()
