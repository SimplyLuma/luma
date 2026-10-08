# SPDX-License-Identifier: Apache-2.0
"""Creator follow-up on real SQLite notes, native widgets and actual exports.

Only the OS chooser destination is controlled. No save/export/restore/menu
handler or native text measurement is replaced.
"""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk
from prairie_apps.notes import NotesApplication
from prairie_apps.notes_lumaui import NotesLumaWindow
from luma_appkit import Island, Toast


def pump(seconds=.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.002)


class CreatorNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = NotesApplication()
        cls.app.set_application_id('org.projectluma.Notes.CreatorRuntime')
        cls.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    @classmethod
    def tearDownClass(cls):
        cls.app.run_dispose()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='notes-native-')
        self.env = mock.patch.dict(os.environ, {'XDG_DATA_HOME': self.directory.name})
        self.env.start()
        # Each test owns the SQLite library; an external device watcher must
        # not consume another test's temporary directory during teardown.
        with mock.patch.object(NotesLumaWindow, '_watch_store'):
            self.window = NotesLumaWindow(self.app)
        self.window.set_default_size(1180, 740)
        self.window.present(); pump(.3)
        self.window.create_note(); pump()

    def tearDown(self):
        self.window._flush_save()
        self.window.destroy(); pump()
        self.env.stop(); self.directory.cleanup()

    def test_empty_and_trailing_paragraph_caret_does_not_jump_when_typing(self):
        w = self.window
        for text in ('', 'First\n', 'First\n\n'):
            with self.subTest(text=text):
                w.page.clear(); w.buffer.set_text(text); pump()
                before = w.editor.get_iter_location(w.buffer.get_end_iter()).y
                w.buffer.insert_at_cursor('X'); pump()
                after = w.editor.get_iter_location(w.buffer.get_end_iter()).y
                self.assertEqual(after, before)
                self.assertEqual(w.page.serialize()[0], text + 'X')

    def test_header_new_note_matches_footer_and_has_shared_tooltip(self):
        w = self.window
        section = next(row for row in self._children(w.sidebar_list)
                       if getattr(row, 'action_button', None) is not None)
        self.assertIn('Notes', section.get_child().get_first_child().get_text())
        self.assertEqual(section.action_button.get_tooltip_text(), 'New note')
        before = len(w.store.list_notes())
        section.action_button.emit('clicked'); pump()
        self.assertEqual(len(w.store.list_notes()), before + 1)
        self.assertIsNone(w.current.folder_id)
        self.assertIsNone(w.store.parent_of(w.current.id))
        self.assertEqual(w.current.body, '')

    def test_slash_filters_and_keyboard_applies_persisted_h1(self):
        w = self.window
        self.assertTrue(w._slash_key(None, Gdk.KEY_slash, 0, Gdk.ModifierType(0)))
        pump(); menu = w.slash_menu
        menu.field.text = 'h1'; pump()
        self.assertEqual([b.get_visible() for b in menu.command_buttons].count(True), 1)
        menu._key(None, Gdk.KEY_Return, 0, 0); pump()
        w.buffer.insert_at_cursor('Heading'); w._flush_save()
        self.assertEqual(w.current.body, 'Heading')
        self.assertTrue(any(r['style'] == 'heading-1' for r in w.current.runs))
        from prairie_apps.notes_export import to_markdown
        self.assertIn('# Heading\n', to_markdown(w.current)[0])
        w._open_note(w.current)
        self.assertIn('heading-1', w.page.block_of_line(0))

    def test_slash_commands_and_escape_preserve_literal_prose(self):
        w = self.window
        for query, style in (('bold','bold'), ('numbered','numbered'),
                             ('bulleted','bulleted'), ('checklist','checklist')):
            with self.subTest(query=query):
                w.create_note()
                self.assertTrue(w._slash_key(None, Gdk.KEY_slash, 0, 0))
                pump(); menu = w.slash_menu; menu.field.text = query; pump()
                menu._key(None, Gdk.KEY_Down, 0, 0)
                menu._key(None, Gdk.KEY_Return, 0, 0); pump()
                w.buffer.insert_at_cursor('Words'); w._flush_save()
                self.assertIn(style, [r['style'] for r in w.current.runs])
                self.assertNotIn('/', w.current.body)
        w.create_note(); w._slash_key(None, Gdk.KEY_slash, 0, 0); pump()
        w.slash_menu._key(None, Gdk.KEY_Escape, 0, 0); pump()
        self.assertEqual(w.page.serialize()[0], '/')
        w.buffer.set_text('https:'); w.buffer.place_cursor(w.buffer.get_end_iter())
        self.assertFalse(w._slash_key(None, Gdk.KEY_slash, 0, 0))

    def test_slash_link_and_image_use_actual_native_insert_handlers(self):
        w = self.window
        w._slash_key(None, Gdk.KEY_slash, 0, 0); pump()
        w.slash_menu.field.text = 'link'; pump()
        w.slash_menu._key(None, Gdk.KEY_Return, 0, 0); pump()
        fields = []
        def visit(widget):
            from luma_appkit import TextField
            if isinstance(widget, TextField): fields.append(widget)
            for child in self._children(widget): visit(child)
        visit(w.layer_host)
        display = next(f for f in fields if f.label.get_text() == 'Text to display')
        address = next(f for f in fields if f.label.get_text() == 'Link address')
        display.text = 'Luma'; address.text = 'https://simplyluma.com'
        address.entry.emit('activate'); pump(); w._flush_save()
        self.assertEqual(w.current.body, 'Luma')
        self.assertTrue(any(r['style']=='link' and r['href']=='https://simplyluma.com' for r in w.current.runs))
        # The picture chooser alone is controlled; image import, attachment
        # bytes, TextChildAnchor and formatting are the production handlers.
        import base64
        picture = Path(self.directory.name)/'pixel.png'
        picture.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR1kAAAAASUVORK5CYII='))
        class ChosenPicture:
            def __init__(self, **kwargs): pass
            def set_filters(self, filters): pass
            def set_default_filter(self, filter): pass
            def open_multiple(self, parent, cancellable, callback): callback(self, None)
            def open_multiple_finish(self, result):
                files = Gio.ListStore.new(Gio.File)
                files.append(Gio.File.new_for_path(str(picture)))
                return files
        w.create_note()
        with mock.patch('prairie_apps.notes.Gtk.FileDialog', ChosenPicture):
            w._slash_key(None, Gdk.KEY_slash, 0, 0); pump()
            w.slash_menu.field.text = 'image'; pump()
            w.slash_menu._key(None, Gdk.KEY_Return, 0, 0); pump()
        w._flush_save()
        self.assertTrue(w.page.pictures)
        self.assertTrue(any(r['style']=='image' for r in w.current.runs))
        self.assertNotIn('/', w.current.body)

    def test_shared_slash_search_remains_keyboard_operable_at_phone_width(self):
        w = self.window
        w.set_default_size(390, 820); pump(.3)
        w._slash_key(None, Gdk.KEY_slash, 0, 0); pump(.3)
        menu = w.slash_menu
        self.assertTrue(menu.is_open)
        menu.field.text = 'bulleted'; pump()
        menu._key(None, Gdk.KEY_Return, 0, 0); pump()
        w.buffer.insert_at_cursor('Phone item'); w._flush_save()
        self.assertIn('bulleted', [r['style'] for r in w.current.runs])

    def test_leaf_can_accept_child_and_reject_self_or_ancestor_cycle(self):
        w = self.window
        parent = w.current
        child = w.store.create_note(title='Child')
        w._reload_sidebar(); pump()
        row = w.note_rows[parent.id]
        self.assertEqual(row.drop_place(row.get_height()/2), 'inside')
        self.assertTrue(w._tree_can_drop(row, 'note:' + child.id, 'inside'))
        self.assertFalse(w._tree_can_drop(row, 'note:' + parent.id, 'inside'))
        self.assertTrue(w._tree_drop(row, 'note:' + child.id, 'inside'))
        self.assertEqual(w.store.parent_of(child.id), parent.id)
        w.open_nodes.add('n' + parent.id); w._reload_sidebar(); pump()
        self.assertFalse(w._tree_can_drop(w.note_rows[child.id], 'note:' + parent.id, 'inside'))
        self.assertEqual(w.note_rows[child.id].drop_place(0), 'before')
        self.assertEqual(w.note_rows[child.id].drop_place(w.note_rows[child.id].get_height()), 'after')

    def test_nested_folder_above_below_order_is_durable(self):
        w = self.window
        parent = w.store.create_folder('Parent')
        first = w.store.create_folder('First', parent_id=parent.id)
        second = w.store.create_folder('Second', parent_id=parent.id)
        moving = w.store.create_folder('Moving')
        w.store.set_folder_expanded(parent.id, True)
        w._reload_sidebar(); pump()
        self.assertTrue(w._tree_drop(w.folder_rows[second.id], 'folder:'+moving.id, 'before'))
        def order():
            return [f.id for f in w.store.root_items()
                    if hasattr(f, 'name') and w.store.folder_parents().get(f.id) == parent.id]
        self.assertEqual(order(), [first.id, moving.id, second.id])
        self.assertTrue(w._tree_drop(w.folder_rows[second.id], 'folder:'+moving.id, 'after'))
        self.assertEqual(order(), [first.id, second.id, moving.id])
        self.assertFalse(w._tree_can_drop(w.folder_rows[moving.id], 'folder:'+parent.id, 'inside'))
        w._reload_sidebar()
        self.assertEqual(order(), [first.id, second.id, moving.id])

    def test_native_drag_preview_uses_visible_row_and_straight_insert_line(self):
        w = self.window; row = w.note_rows[w.current.id]
        source = mock.Mock()
        row._drag_begin(source, None)
        paintable = source.set_icon.call_args.args[0]
        self.assertIsInstance(paintable, Gtk.WidgetPaintable)
        self.assertIs(paintable.get_widget(), row.get_child())
        row._mark_drop('after'); pump()
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(row).snapshot(snapshot, row.get_width(), row.get_height())
        strips = []
        def inspect(node):
            if node is None: return
            rect = node.get_bounds()
            if hasattr(node, 'get_color') and rect.get_width() == row.get_width() and rect.get_height() == 2:
                strips.append(rect)
            if hasattr(node, 'get_n_children'):
                for i in range(node.get_n_children()): inspect(node.get_child(i))
            elif hasattr(node, 'get_child'): inspect(node.get_child())
        inspect(snapshot.to_node())
        self.assertTrue(strips, 'insertion is a full-width native 2px straight color node')
        row._mark_drop(None)

    def test_glow_and_grain_are_inside_actual_rounded_island(self):
        w = self.window
        self.assertIsInstance(w.light.get_ancestor(Island), Island)
        self.assertIs(w.grain.get_ancestor(Island), w.light.get_ancestor(Island))
        self.assertIs(w.light.get_ancestor(Island).get_overflow(), Gtk.Overflow.HIDDEN)
        self.capture('notes-rounded-island')

    def test_delete_visible_undo_restores_saved_body_selection_and_hidden_nested_path(self):
        w = self.window
        outer = w.store.create_folder('Outer')
        inner = w.store.create_folder('Inner', parent_id=outer.id)
        parent = w.store.create_note(title='Parent', folder_id=inner.id)
        child = w.store.create_note(title='Child', folder_id=inner.id)
        w.store.move_note(child.id, inner.id, parent_id=parent.id)
        w.store.update_note(child.id, title='Child', body='Saved body')
        w._reload_sidebar(); w._open_note(w.store.get_note(child.id)); pump()
        w._delete_specific(w.current); pump(.35)
        self.assertIsNotNone(w.store.get_note(child.id).deleted_at)
        toast = next(t for t in self.current_toasts() if t.kind == 'deleted')
        button = toast.get_last_child()
        ok, bounds = button.compute_bounds(w)
        self.assertTrue(ok)
        picked = w.pick(bounds.get_x()+bounds.get_width()/2,
                        bounds.get_y()+bounds.get_height()/2, Gtk.PickFlags.DEFAULT)
        self.assertTrue(picked is button or picked.is_ancestor(button), 'Undo is pointer reachable')
        button.emit('clicked'); pump()
        self.assertIsNone(w.store.get_note(child.id).deleted_at)
        self.assertEqual((w.current.id, w.current.body), (child.id, 'Saved body'))
        self.assertEqual(w.store.parent_of(child.id), parent.id)
        self.assertIn(child.id, w.note_rows)
        self.assertIs(w.sidebar_list.get_selected_row(), w.note_rows[child.id])
        self.assertEqual(w.store.folder_parents()[inner.id], outer.id)

    def test_failed_save_does_not_delete_note_and_explicit_save_reports_after_persistence(self):
        w = self.window; note = w.current
        w.buffer.set_text('Unsaved words')
        with mock.patch.object(w.store, 'update_note', side_effect=OSError('disk full')):
            w._delete_specific(note)
        self.assertIsNone(w.store.get_note(note.id).deleted_at)
        self.assertEqual(w.page.serialize()[0], 'Unsaved words')
        self.assertEqual(w.current.id, note.id)
        w._key_pressed(None, Gdk.KEY_s, 0, Gdk.ModifierType.CONTROL_MASK); pump()
        self.assertEqual(w.store.get_note(note.id).body, 'Unsaved words')
        self.assertTrue(any(t.kind=='saved' for t in self.current_toasts()))

    def test_failed_export_has_error_toast_and_no_success_notification(self):
        w = self.window; w.buffer.set_text('Export failure'); w._flush_save()
        destination = Path(self.directory.name)/'missing-parent'/'Note.md'
        class ChosenDestination:
            def __init__(self, **kwargs): pass
            def save(self, parent, cancellable, callback): callback(self, None)
            def save_finish(self, result): return Gio.File.new_for_path(str(destination))
        with mock.patch('prairie_apps.notes_lumaui.Gtk.FileDialog', ChosenDestination):
            w._export_note(w.current)
        for _ in range(100):
            pump(.02)
            current = [t for t in self.current_toasts() if t.kind == 'error']
            if current: break
        self.assertFalse(destination.exists())
        self.assertTrue(current)
        self.assertIn('Could not export', current[-1].message)
        self.assertFalse(any(t.kind=='saved' for t in self.current_toasts()))

    def test_actual_markdown_export_reports_success_only_after_saved_bytes(self):
        w = self.window
        w.buffer.set_text('Actual export'); w._flush_save()
        destination = Path(self.directory.name)/'Saved note.md'
        class ChosenDestination:
            def __init__(self, **kwargs): pass
            def save(self, parent, cancellable, callback): callback(self, None)
            def save_finish(self, result): return Gio.File.new_for_path(str(destination))
        with mock.patch('prairie_apps.notes_lumaui.Gtk.FileDialog', ChosenDestination):
            w._export_note(w.current)
        for _ in range(100):
            pump(.02)
            current = [t for t in self.current_toasts() if t.kind == 'saved']
            if current: break
        self.assertTrue(destination.is_file())
        self.assertIn('Actual export', destination.read_text())
        self.assertTrue(current, 'successful native export must show an accepted kit toast')
        self.assertIn('Exported', current[-1].message)

    def test_markdown_copy_reaches_an_actual_registered_file_receiver(self):
        import json
        from luma_appkit import ShareTarget
        w = self.window; w.buffer.set_text('Receiver contents'); w._flush_save()
        subject = w.current
        directory = Path(self.directory.name)
        receipt = directory/'receipt.json'
        helper = directory/'receiver.py'
        helper.write_text("import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1]);Path(sys.argv[2]).write_text(json.dumps({'file':str(p),'text':p.read_text()}))\n")
        desktop = directory/'org.projectluma.NotesTestReceiver.desktop'
        desktop.write_text(f'[Desktop Entry]\nType=Application\nName=Notes test receiver\nExec=/usr/bin/python3 {helper} %f {receipt}\nMimeType=text/markdown;\nTerminal=false\n')
        receiver = Gio.DesktopAppInfo.new_from_filename(str(desktop))
        self.assertTrue(receiver.supports_files())
        with mock.patch('prairie_apps.notes_lumaui.Gio.DesktopAppInfo.new', return_value=receiver):
            w._send_copy_to_app(subject, ShareTarget('receiver','Notes test receiver','org.projectluma.NotesTestReceiver'))
            for _ in range(100):
                pump(.03)
                if receipt.is_file(): break
            self.assertTrue(receipt.is_file(), 'a separate actual launched receiver must obtain the exported file')
            delivered = json.loads(receipt.read_text())
            self.assertIn('Receiver contents', delivered['text'])
            self.assertTrue(Path(delivered['file']).is_file())
            self.assertEqual(w.store.get_note(subject.id), subject)
            self.assertTrue(any(t.kind=='sent' for t in self.current_toasts()))

    def test_readonly_shortcuts_and_structural_callbacks_preserve_body_and_formatting(self):
        w = self.window
        w.buffer.set_text('A shared paragraph')
        w.buffer.select_range(w.buffer.get_start_iter(), w.buffer.get_end_iter())
        w._toggle_format('bold'); w._flush_save()
        saved = w.store.get_note(w.current.id)
        original = w.page.serialize()
        pending = dict(w.page.pending)
        w.editor.set_editable(False); w.title_entry.set_editable(False)
        w._render_tools(); w.editor.grab_focus(); pump()
        self.assertEqual(w.format_toolbar._state, 'hidden')
        self.assertTrue(w._in_body())
        for key, modifiers in ((Gdk.KEY_b, Gdk.ModifierType.CONTROL_MASK),
                               (Gdk.KEY_i, Gdk.ModifierType.CONTROL_MASK),
                               (Gdk.KEY_u, Gdk.ModifierType.CONTROL_MASK),
                               (Gdk.KEY_k, Gdk.ModifierType.CONTROL_MASK),
                               (Gdk.KEY_y, Gdk.ModifierType.CONTROL_MASK),
                               (Gdk.KEY_z, Gdk.ModifierType.CONTROL_MASK),
                               (Gdk.KEY_z, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)):
            self.assertTrue(w._key_pressed(None, key, 0, modifiers))
        w._plain_text(); w._set_block_style('heading-1'); w._insert_divider()
        w._show_link_popover(w.editor); w._choose_picture_file()
        self.assertFalse(w._slash_key(None, Gdk.KEY_slash, 0, Gdk.ModifierType(0)))
        w.page.toggle_inline('italic'); w.page.toggle_block('bulleted')
        w.page.set_alignment(w.page.selected_lines(), 'align-center')
        self.assertFalse(w.page.handle_return())
        self.assertFalse(w.page.handle_backspace())
        self.assertFalse(w.page.indent(1))
        w._flush_save(); pump()
        self.assertEqual(w.page.serialize(), original)
        self.assertEqual(w.page.pending, pending)
        self.assertEqual(w.store.get_note(w.current.id), saved)
        with self.assertRaises(PermissionError):
            w._semantic_save('note:' + saved.id, 'Forbidden replacement')
        self.assertTrue(w._finish_note_rename(saved, 'Forbidden title'))
        row = w.note_rows[saved.id]
        row.begin_rename()
        self.assertFalse(row.is_renaming())
        self.assertEqual(w.store.get_note(saved.id), saved)
        # A non-current note must consult its own role, rather than the open
        # page's editable state. A stale rename commit cannot change either.
        other = w.store.create_note(title='Other shared note', body='Other text')
        with mock.patch.object(w, '_collaboration_role', side_effect=lambda note_id: 'view' if note_id == other.id else 'owner'):
            with self.assertRaises(PermissionError):
                w._semantic_save('note:' + other.id, 'Forbidden other replacement')
            self.assertTrue(w._finish_note_rename(other, 'Forbidden other title'))
        self.assertEqual(w.store.get_note(other.id), other)
        # The programmatic remote-render path still applies a genuine newer
        # saved snapshot while the user-facing editor remains read-only.
        w.page.load('Remote update', ({'start': 0, 'end': 13, 'style': 'italic'},))
        self.assertEqual(w.page.serialize(), ('Remote update', ({'start': 0, 'end': 13, 'style': 'italic'},)))
        w.page.load(saved.body, saved.runs)
        w.editor.set_editable(True); w.title_entry.set_editable(True); w._render_tools()
        self.assertNotEqual(w.format_toolbar._state, 'hidden')
        w.buffer.select_range(w.buffer.get_start_iter(), w.buffer.get_end_iter())
        w._toggle_format('italic'); w._flush_save()
        self.assertNotEqual(w.store.get_note(w.current.id).runs, saved.runs)

    def current_toasts(self):
        toast = Toast._current.get(id(self.window.document_host))
        return [toast] if toast is not None else []

    @staticmethod
    def _children(widget):
        child = widget.get_first_child()
        while child:
            yield child
            child = child.get_next_sibling()

    def capture(self, name):
        directory = os.environ.get('NOTES_CREATOR_CAPTURE_DIR')
        if not directory: return
        pump(.3); w = self.window; snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(w).snapshot(snapshot, w.get_width(), w.get_height())
        node = snapshot.to_node()
        self.assertIsNotNone(node)
        self.assertTrue(w.get_renderer().render_texture(node, None).save_to_png(str(Path(directory)/(name+'.png'))))


if __name__ == '__main__': unittest.main(verbosity=2)
