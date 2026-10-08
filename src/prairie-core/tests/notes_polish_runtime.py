#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Notes: lists, the title, menus, folders, pictures and saving, in real windows.

Each test drives a real Notes window under Xvfb over an invented library in a
private home. Keys go through the editor's own key handling (the same handler a
key press reaches), pictures through the same paste and drop paths another app
would use.

1. Bullets and numbers are drawn beside the text, in the text's colour, in
   light and in dark, and numbering follows the list (1., a., i.).
2. Enter continues a list; Enter on an empty item leaves it (or steps out).
3. Backspace at the start of an item outdents, then takes the bullet off.
4. Enter in the title goes to the start of the page; the title is one line.
5. "Untitled page" is only a placeholder; real titles select with Ctrl+A.
6. A page's right-click menu has Open, Rename, Duplicate, Pin, Move to,
   Export and Delete (to Recently Deleted, with Undo).
7. The page's text starts a clear gap below the title.
8. Double-clicking a folder renames it without opening or closing it.
9. Pictures paste and drop in, are stored once by content, fit the text
   width, and have their controls and menu.
10. Saving waits for a pause but never more than a few seconds, and skips
    writes that change nothing.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("GSK_RENDERER", "cairo")
os.environ.setdefault("PRAIRIE_EDS_MODE", "disabled")
_home = tempfile.mkdtemp(prefix="notes-polish-")
for variable, leaf in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                       ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
    os.environ[variable] = os.path.join(_home, leaf)
    os.makedirs(os.environ[variable], exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Graphene, Gtk  # noqa: E402

from prairie_apps import notes as notes_module  # noqa: E402
from prairie_apps.notes_lumaui import NotesLumaWindow  # noqa: E402
from prairie_apps.notes_attachments import attachments_directory, store_picture  # noqa: E402
from prairie_apps.notes_backend import NotesStore, notes_data_directory  # noqa: E402
from prairie_apps.notes_richtext import CARRIER, list_markers, number_label  # noqa: E402

# Select the requested appearance on the private test bus, independently of
# the user's desktop. The kit now loads its LumaUI families from this choice;
# the old token-sheet environment variables alone do not choose those sheets.
mock.patch("luma_appkit.widgets._prefers_dark",
           lambda: os.environ.get("NOTES_TEST_APPEARANCE", "light") == "dark").start()
context = GLib.MainContext.default()
_application = None


def pump(seconds: float = 0.05) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def png_bytes(width: int, height: int, rgba: int) -> bytes:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
    pixbuf.fill(rgba)
    ok, data = pixbuf.save_to_bufferv("png", [], [])
    assert ok
    return bytes(data)


class Key:
    """Press a key through the editor's own key handler."""

    def __init__(self, page) -> None:
        self.page = page

    def __call__(self, keyval: int, state: Gdk.ModifierType = Gdk.ModifierType(0)) -> bool:
        return self.page._key_pressed(None, keyval, 0, state)


def type_text(buffer: Gtk.TextBuffer, text: str) -> None:
    """Type as the text view does: one interactive insert at the caret per letter."""
    for letter in text:
        buffer.insert_interactive_at_cursor(letter, -1, True)


class NotesWindowCase(unittest.TestCase):
    app: Adw.Application
    window_type = notes_module.NotesWindow

    @classmethod
    def setUpClass(cls) -> None:
        global _application
        if _application is None:
            _application = notes_module.NotesApplication()
            _application.set_flags(Gio.ApplicationFlags.NON_UNIQUE | Gio.ApplicationFlags.HANDLES_OPEN)
            assert _application.register(None)
            notes_module.install_appkit()
            notes_module._install_notes_style()
            # The explicit package sheets select the kit palette; stock GTK's
            # text nodes also need the matching libadwaita appearance.
            manager = Adw.StyleManager.get_default()
            dark = os.environ.get("NOTES_TEST_APPEARANCE", "light") == "dark"
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
            assert manager.get_dark() == dark
        cls.app = _application

    def setUp(self) -> None:
        path = notes_data_directory() / "notes.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(str(path) + suffix)
            except FileNotFoundError:
                pass
        store = NotesStore(path)
        self.folder = store.create_folder("Projects")
        self.first = store.create_note(title="Shopping")
        store.create_note(folder_id=self.folder.id, title="Inside")
        store.close()
        self.window = self.window_type(self.app)
        self.window.set_default_size(900, 640)
        self.window.present()
        pump(0.4)
        self.window._open_note(self.window.store.get_note(self.first.id))
        pump(0.2)
        self.page = self.window.page
        self.buffer = self.window.buffer
        self.key = Key(self.page)
        self.window.editor.grab_focus()

    def tearDown(self) -> None:
        self.window.close()
        pump(0.1)

    def lines(self) -> list[str]:
        body, _runs = self.page.serialize()
        return body.split("\n")

    def block(self, line: int) -> frozenset[str]:
        return self.page.block_of_line(line)

    def start_list(self, style: str, text: str) -> None:
        type_text(self.buffer, text)
        self.window._toggle_format(style)


class ListTests(NotesWindowCase):
    # Production activation opens this composition. The legacy window remains
    # available for the existing command/rename compatibility tests below.
    window_type = NotesLumaWindow

    def test_numbering_follows_the_list(self) -> None:
        blocks = [frozenset({"numbered"}), frozenset({"numbered"}), frozenset({"numbered", "indent-1"}),
                  frozenset({"numbered", "indent-1"}), frozenset({"numbered"}), frozenset(),
                  frozenset({"numbered"}), frozenset({"bulleted"}), frozenset({"bulleted", "indent-1"})]
        markers = list_markers(blocks)
        self.assertEqual([markers[line][0] for line in (0, 1, 2, 3, 4)], ["1.", "2.", "a.", "b.", "3."])
        self.assertNotIn(5, markers)
        self.assertEqual(markers[6][0], "1.", "a paragraph between lists starts the count again")
        self.assertEqual((markers[7][0], markers[8][0]), ("•", "◦"))
        self.assertEqual(number_label(2, 4), "iv.")
        self.assertEqual(number_label(1, 28), "ab.")

    def test_bullets_are_drawn_and_visible(self) -> None:
        """Run once in each appearance: %check runs this file light, then dark."""
        appearance = os.environ.get("NOTES_TEST_APPEARANCE", "light")
        self.start_list("bulleted", "Apples")
        self.key(Gdk.KEY_Return)
        type_text(self.buffer, "Pears")
        self.window._toggle_format("numbered")
        pump(0.4)
        self.assertEqual(self.page.markers(), {0: ("•", 0), 1: ("1.", 0)})
        view = self.window.editor
        # v70's document column is transparent over the shared Island. Render
        # that enclosing surface so the actual page background is included.
        canvas = self.window.document_host
        texture = self._render(canvas)
        texture.save_to_png(os.path.join(os.environ.get("NOTES_TEST_SHOTS", _home), f"markers-{appearance}.png"))
        ok, origin = view.compute_point(canvas, Graphene.Point())
        self.assertTrue(ok)
        width, height = texture.get_width(), texture.get_height()
        downloader = Gdk.TextureDownloader.new(texture)
        downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
        downloaded, stride = downloader.download_bytes()
        pixels = bytes(downloaded.get_data())

        def luminance(x: int, y: int) -> float:
            offset = y * stride + x * 4
            red, green, blue = pixels[offset:offset + 3]
            return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255

        view_left, view_top = int(origin.x), int(origin.y)
        view_right = min(width, view_left + view.get_width())
        view_bottom = min(height, view_top + view.get_height())
        self.assertGreater(view_right - view_left, 3, "the body is allocated")
        self.assertGreater(view_bottom - view_top, 3, "the body is allocated")
        # Sample the empty right edge of the body, inside the painted Island
        # rather than its transparent rounded corner or the action center.
        sample_offset = (view_top + 3) * stride + (view_right - 3) * 4
        self.assertEqual(pixels[sample_offset + 3], 255, "the actual page background is painted")
        page = luminance(view_right - 3, view_top + 3)
        self.assertEqual(page < 0.5, appearance == "dark", f"the page is not {appearance}")
        # Find each body line by its ink, then split the line into runs of
        # ink columns: the marker is the first run, standing apart from the
        # text, and it must stand out from the page as much as the text does.

        def inked(value: float) -> bool:
            return abs(value - page) > 0.2

        rows = [y for y in range(view_top, view_bottom)
                if any(inked(luminance(x, y)) for x in range(view_left, view_right))]
        bands: list[list[int]] = []
        for y in rows:
            if bands and y - bands[-1][-1] <= 2:
                bands[-1].append(y)
            else:
                bands.append([y])
        self.assertGreaterEqual(len(bands), 2, "two list lines are drawn")
        for line, band in enumerate(bands[:2]):
            columns = sorted({x for y in band for x in range(view_left, view_right) if inked(luminance(x, y))})
            runs: list[list[int]] = []
            for x in columns:
                if runs and x - runs[-1][-1] <= 3:
                    runs[-1].append(x)
                else:
                    runs.append([x])
            self.assertGreaterEqual(len(runs), 2, f"line {line + 1} has a marker apart from its text")
            marker = runs[0]
            ink = [luminance(x, y) for y in band for x in range(marker[0], marker[-1] + 1)]
            contrast = max(abs(value - page) for value in ink)
            self.assertGreater(contrast, 0.45, f"the marker on line {line + 1} is not visible in {appearance}")
            self.assertLess(marker[-1] - marker[0], 24, f"the marker on line {line + 1} is a marker, not text")
        text = view.get_color()
        text_luminance = 0.2126 * text.red + 0.7152 * text.green + 0.0722 * text.blue
        self.assertGreater(abs(text_luminance - page), 0.45, "markers take the text colour")

    @staticmethod
    def _render(widget: Gtk.Widget) -> Gdk.Texture:
        paintable = Gtk.WidgetPaintable.new(widget)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, widget.get_width(), widget.get_height())
        node = snapshot.to_node()
        renderer = widget.get_native().get_renderer()
        bounds = Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height())
        return renderer.render_texture(node, bounds)

    def test_enter_continues_a_list_and_leaves_it_on_an_empty_item(self) -> None:
        self.start_list("bulleted", "one")
        self.assertTrue(self.key(Gdk.KEY_Return))
        type_text(self.buffer, "two")
        self.assertEqual(self.block(1), frozenset({"bulleted"}), "the new line is the next item")
        self.assertTrue(self.key(Gdk.KEY_Return))
        self.assertEqual(self.block(2), frozenset({"bulleted"}), "an empty item still has its bullet")
        self.assertIn(2, self.page.markers())
        self.assertTrue(self.key(Gdk.KEY_Return), "Enter on the empty item")
        self.assertEqual(self.block(2), frozenset(), "Enter on an empty item leaves the list")
        type_text(self.buffer, "after")
        self.assertEqual(self.lines(), ["one", "two", "after"])
        body, runs = self.page.serialize()
        self.assertNotIn(CARRIER, body)
        self.assertIn({"start": 0, "end": 8, "style": "bulleted"}, list(runs))

    def test_enter_on_an_empty_nested_item_steps_out_one_level(self) -> None:
        self.start_list("numbered", "one")
        self.key(Gdk.KEY_Return)
        self.assertTrue(self.key(Gdk.KEY_Tab), "Tab indents a list item")
        self.assertEqual(self.block(1), frozenset({"numbered", "indent-1"}))
        self.key(Gdk.KEY_Return)
        self.assertEqual(self.block(1), frozenset({"numbered"}), "out one level, still in the list")
        type_text(self.buffer, "two")
        self.assertEqual(self.page.markers()[1][0], "2.")

    def test_enter_in_the_middle_splits_and_after_a_heading_does_not_continue_it(self) -> None:
        self.start_list("bulleted", "applepie")
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(5))
        self.key(Gdk.KEY_Return)
        self.assertEqual(self.lines(), ["apple", "pie"])
        self.assertEqual((self.block(0), self.block(1)), (frozenset({"bulleted"}), frozenset({"bulleted"})))
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.key(Gdk.KEY_Return)
        self.key(Gdk.KEY_Return)
        type_text(self.buffer, "Title")
        self.window._toggle_format("heading")
        self.key(Gdk.KEY_Return)
        self.assertEqual(self.block(3), frozenset(), "the paragraph after a heading is body text")

    def test_typing_in_a_list_item_keeps_its_bullet(self) -> None:
        self.start_list("bulleted", "ple")
        self.buffer.place_cursor(self.buffer.get_start_iter())
        type_text(self.buffer, "Ap")
        self.assertEqual(self.lines(), ["Apple"])
        self.assertEqual(self.block(0), frozenset({"bulleted"}))


class BackspaceTests(NotesWindowCase):
    def test_backspace_at_the_start_takes_the_bullet_off(self) -> None:
        self.start_list("bulleted", "one")
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.assertTrue(self.key(Gdk.KEY_BackSpace))
        self.assertEqual(self.block(0), frozenset())
        self.assertEqual(self.lines(), ["one"], "the text stays")
        self.assertFalse(self.key(Gdk.KEY_BackSpace), "then Backspace is an ordinary Backspace")

    def test_backspace_outdents_a_nested_item_first(self) -> None:
        self.start_list("bulleted", "one")
        self.key(Gdk.KEY_Return)
        self.key(Gdk.KEY_Tab)
        type_text(self.buffer, "two")
        self.buffer.place_cursor(self.page._line_start(1))
        self.assertTrue(self.key(Gdk.KEY_BackSpace))
        self.assertEqual(self.block(1), frozenset({"bulleted"}), "out one level")
        self.assertTrue(self.key(Gdk.KEY_BackSpace))
        self.assertEqual(self.block(1), frozenset(), "then the bullet goes")
        self.assertEqual(self.lines(), ["one", "two"])

    def test_backspace_in_the_middle_of_an_item_deletes_a_letter(self) -> None:
        self.start_list("bulleted", "one")
        self.assertFalse(self.key(Gdk.KEY_BackSpace))

    def test_shift_tab_outdents(self) -> None:
        self.start_list("bulleted", "one")
        self.key(Gdk.KEY_Tab)
        self.assertTrue(self.key(Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.SHIFT_MASK))
        self.assertEqual(self.block(0), frozenset({"bulleted"}))


class TitleTests(NotesWindowCase):
    def test_enter_in_the_title_goes_to_the_start_of_the_page(self) -> None:
        type_text(self.buffer, "Body text")
        self.window.title_entry.grab_focus()
        self.window.title_entry.emit("activate")
        pump(0.1)
        self.assertTrue(self.window._in_body())
        self.assertEqual(self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset(), 0)
        self.assertTrue(self.window.title_entry.get_property("truncate-multiline"))

    def test_untitled_is_only_a_placeholder(self) -> None:
        self.window.create_note()
        pump(0.1)
        title = self.window.title_entry
        self.assertEqual(title.get_text(), "", "a new page has no title text to select")
        self.assertEqual(title.get_placeholder_text(), "Untitled page")
        self.assertIs(self.window.get_focus().get_ancestor(Gtk.Entry), title)
        title.set_text("P")
        self.assertEqual(title.get_text(), "P", "the first keystroke replaces the placeholder")
        title.set_text("Plans for June")
        title.select_region(0, -1)
        self.assertEqual(title.get_selection_bounds(), (0, len("Plans for June")))
        self.window._flush_save()
        stored = self.window.store.get_note(self.window.current.id)
        self.assertEqual(stored.title, "Plans for June")

    def test_a_stored_placeholder_title_opens_as_no_title(self) -> None:
        note = self.window.store.create_note(title="Untitled page")
        self.window._open_note(note)
        self.assertEqual(self.window.title_entry.get_text(), "")

    def test_duplicate_of_an_untitled_page_is_untitled(self) -> None:
        self.window.create_note()
        self.window.duplicate_note()
        self.assertEqual(self.window.current.title, "")


class MenuTests(NotesWindowCase):
    def labels(self, registry) -> list[str]:
        return [command.label for group in registry.groups for command in group.commands]

    def test_a_page_menu_has_every_choice_and_opens(self) -> None:
        note = self.window.store.get_note(self.first.id)
        registry = self.window.note_commands(note)
        self.assertEqual(self.labels(registry), ["Open", "Rename", "Duplicate", "Pin", "Move to",
                                                 "Export as Markdown…", "Delete"])
        moves = next(c for g in registry.groups for c in g.commands if c.id == "note.move").children
        self.assertIn("Projects", [command.label for command in moves])
        delete = next(c for g in registry.groups for c in g.commands if c.id == "note.delete")
        self.assertTrue(delete.destructive)
        rename = next(c for g in registry.groups for c in g.commands if c.id == "note.rename")
        self.assertEqual(rename.shortcut, ("F2",))
        self.assertEqual(rename.description, "")
        # Right-click the row: the menu is built and shown.
        row = self.window.note_rows[note.id]
        shown = []
        with mock.patch.object(Gtk.Popover, "popup", lambda popover: shown.append(popover)):
            gesture = Gtk.GestureClick()
            row._context_menu(gesture, 1, 20, 10)
        self.assertEqual(len(shown), 1)

    def test_pin_and_move_and_delete_with_undo(self) -> None:
        note = self.window.store.get_note(self.first.id)
        self.window.note_commands(note).invoke("note.pin")
        self.assertTrue(self.window.store.get_note(note.id).favorite)
        self.assertEqual(self.window.note_commands(self.window.store.get_note(note.id)).get("note.pin").label, "Unpin")
        self.window.note_commands(note).invoke(f"note.move.{self.folder.id}")
        self.assertEqual(self.window.store.get_note(note.id).folder_id, self.folder.id)
        toasts = []
        with mock.patch.object(self.window.toast_overlay, "add_toast", lambda toast: toasts.append(toast)):
            self.window.note_commands(self.window.store.get_note(note.id)).invoke("note.delete")
        self.assertIsNotNone(self.window.store.get_note(note.id).deleted_at, "moved to Recently Deleted")
        self.assertEqual(toasts[0].get_button_label(), "Undo")
        toasts[0].emit("button-clicked")
        self.assertIsNone(self.window.store.get_note(note.id).deleted_at, "Undo brings it back")

    def test_export_writes_markdown_with_lists(self) -> None:
        from prairie_apps.notes_export import export_markdown
        self.start_list("bulleted", "Apples")
        self.key(Gdk.KEY_Return)
        type_text(self.buffer, "Pears")
        self.window._flush_save()
        target = os.path.join(_home, "export.md")
        export_markdown(self.window.store.get_note(self.first.id), target)
        with open(target, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "# Shopping\n\n- Apples\n- Pears\n")


class LayoutTests(NotesWindowCase):
    window_type = NotesLumaWindow

    def test_the_page_starts_a_clear_gap_below_the_title(self) -> None:
        pump(0.2)
        title = self.window.title_entry
        ok, bounds = title.compute_bounds(self.window.canvas)
        ok2, body = self.window.editor.compute_bounds(self.window.canvas)
        self.assertTrue(ok and ok2)
        gap = body.get_y() - (bounds.get_y() + bounds.get_height())
        # Studio v70 #n-title specifies margin: 0 0 14px (line 668).
        self.assertGreaterEqual(gap, 14, "v70's clear gap below the title")
        self.assertLessEqual(gap, 16, "the title gap stays within v70's geometry tolerance")


class FolderTests(NotesWindowCase):
    def test_double_click_renames_a_folder_without_opening_or_closing_it(self) -> None:
        row = self.window.folder_rows[self.folder.id]
        expanded = self.window.store.get_folder(self.folder.id).expanded
        self.window._row_activated(self.window.sidebar_list, row)   # the first click
        row._pressed(None, 2, 10, 10)                               # the second
        pump(0.6)
        row = self.window.folder_rows[self.folder.id]
        self.assertTrue(row.is_renaming(), "inline rename is open")
        self.assertEqual(self.window.store.get_folder(self.folder.id).expanded, expanded)

    def test_a_single_click_opens_and_closes_the_folder_in_place(self) -> None:
        row = self.window.folder_rows[self.folder.id]
        children = list(self.window.folder_children[self.folder.id])
        self.window._row_activated(self.window.sidebar_list, row)
        pump(0.6)
        self.assertIs(self.window.folder_rows[self.folder.id], row, "the row was not rebuilt")
        self.assertFalse(self.window.store.get_folder(self.folder.id).expanded)
        self.assertFalse(any(child.get_visible() for child in children))

    def test_opening_a_page_keeps_the_rows_so_a_double_click_can_rename_it(self) -> None:
        note = self.window.store.list_notes(folder_id=self.folder.id)[0]
        row = self.window.note_rows[note.id]
        self.window._row_activated(self.window.sidebar_list, row)
        self.assertIs(self.window.note_rows[note.id], row)
        row.begin_rename()
        pump(0.1)
        self.assertEqual(row.title_stack.get_visible_child_name(), "rename")


class PictureTests(NotesWindowCase):
    def test_a_picture_pasted_from_another_app_is_kept_once_and_fits_the_text(self) -> None:
        data = png_bytes(1600, 400, 0x3366AAFF)
        clipboard = self.window.editor.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_bytes("image/png", GLib.Bytes.new(data)))
        pump(0.1)
        self.window.editor.emit("paste-clipboard")
        pump(0.6)
        self.assertEqual(len(self.page.pictures), 1)
        picture = self.page.pictures[0]
        pump(0.3)
        self.assertLessEqual(picture.display_size()[0], self.page.column_width())
        self.assertGreater(picture.display_size()[0], 200)
        body, runs = self.page.serialize()
        images = [run for run in runs if run["style"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(body[images[0]["start"]], "￼")
        stored = sorted(attachments_directory().iterdir())
        self.assertIn(images[0]["src"], [path.name for path in stored])
        # The same picture again is the same file.
        self.page.import_bytes(data)
        self.assertEqual(len(sorted(attachments_directory().glob("*.png"))), len([p for p in stored if p.suffix == ".png"]))

    def test_pictures_survive_saving_and_reopening(self) -> None:
        name = store_picture(png_bytes(300, 200, 0xAA6633FF))
        type_text(self.buffer, "Before")
        self.page.insert_picture(name)
        type_text(self.buffer, "After")
        self.window._flush_save()
        other = self.window.store.list_notes(folder_id=self.folder.id)[0]
        self.window._open_note(other)
        self.window._open_note(self.window.store.get_note(self.first.id))
        pump(0.2)
        self.assertEqual([picture.name for picture in self.page.pictures], [name])
        self.assertEqual(self.lines(), ["Before", "￼", "After"])

    def test_a_picture_file_dropped_in_lands_where_it_was_dropped(self) -> None:
        path = os.path.join(_home, "dropped.png")
        with open(path, "wb") as stream:
            stream.write(png_bytes(64, 64, 0x22AA44FF))
        type_text(self.buffer, "Top")
        files = Gdk.FileList.new_from_list([Gio.File.new_for_path(path)])
        self.assertTrue(self.page._dropped(None, files, 5, 5))
        self.assertEqual(len(self.page.pictures), 1)

    def test_picture_controls_and_menu_match_write(self) -> None:
        name = store_picture(png_bytes(900, 300, 0x5555AAFF))
        self.page.insert_picture(name)
        picture = self.page.pictures[0]
        pump(0.2)
        registry = self.window._picture_registry(picture)
        labels = [command.label for group in registry.groups for command in group.commands]
        self.assertEqual(labels, ["Align Left", "Align Center", "Align Right", "Fit to Text Width", "Reset Size",
                                  "Copy Picture", "Replace Picture…", "Delete"])
        registry.invoke("picture.center")
        self.assertIn("align-center", self.page.block_of_line(self.page.picture_line(picture)))
        registry.invoke("picture.full")
        self.assertEqual(picture.display_size()[0], self.page.column_width())
        self.page.choose_picture(picture)
        pump(0.1)
        self.assertIsNotNone(self.window.picture_bar)
        self.assertTrue(picture.handle.get_visible(), "the resize handle shows on the chosen picture")
        registry.invoke("picture.delete")
        self.assertEqual(self.page.pictures, [])
        body, _runs = self.page.serialize()
        self.assertNotIn("￼", body)

    def test_a_picture_the_host_would_not_take_says_so_quietly(self) -> None:
        from prairie_apps.notes_attachments import picture_digest, write_sync_status
        full = store_picture(png_bytes(400, 200, 0x3388CCFF))
        large = store_picture(png_bytes(500, 200, 0xCC8833FF))
        self.page.insert_picture(full)
        self.page.insert_picture(large)
        first, second = self.page.pictures
        pump(0.2)
        self.assertFalse(first.status.get_visible(), "a synced picture shows nothing")
        write_sync_status({"pictures": {picture_digest(full): "note_storage_full",
                                        picture_digest(large): "note_image_too_large"},
                           "quota_bytes": 1073741824, "used_bytes": 1073741824})
        for _ in range(40):
            pump(0.05)
            if first.status.get_visible():
                break
        self.assertTrue(first.status.get_visible(), "the page follows the sync service's status file")
        self.assertEqual(first.status_label.get_label(),
                         "Not synced · Your Luma storage for note images is full. Remove images from notes to free space.")
        self.assertFalse(first.shrink_button.get_visible(), "nothing to press: removing pictures is the fix")
        self.assertIn("1.1\u00a0GB", first.status.get_tooltip_text() or "")
        self.assertTrue(second.status.get_visible())
        self.assertTrue(second.shrink_button.get_visible())
        pump(0.3)
        appearance = os.environ.get("NOTES_TEST_APPEARANCE", "light")
        ListTests._render(self.window.canvas).save_to_png(
            os.path.join(os.environ.get("NOTES_TEST_SHOTS", _home), f"picture-status-{appearance}.png"))
        second.shrink_button.emit("clicked")
        pump(0.2)
        self.assertNotEqual(second.name, large, "a smaller copy takes the picture's place")
        self.assertTrue(second.name.endswith(".jpg"), "an opaque picture is re-encoded as JPEG")
        self.assertFalse(second.status.get_visible(), "the smaller copy has not been refused")
        body, runs = self.page.serialize()
        self.assertIn(second.name, [run.get("src") for run in runs])
        write_sync_status({"pictures": {}})
        for _ in range(40):
            pump(0.05)
            if not first.status.get_visible():
                break
        self.assertFalse(first.status.get_visible(), "the mark goes once the picture is synced")


class SavingTests(NotesWindowCase):
    def test_continuous_typing_still_saves_every_few_seconds(self) -> None:
        writes = []
        original = self.window.store.update_note

        def counting(*arguments, **keywords):
            writes.append(time.monotonic())
            return original(*arguments, **keywords)

        with mock.patch.object(self.window.store, "update_note", counting):
            started = time.monotonic()
            while time.monotonic() - started < 5.0:
                type_text(self.buffer, "a")
                pump(0.15)
        self.assertGreaterEqual(len(writes), 1, "saved while typing, not only after a pause")
        self.assertLessEqual(len(writes), 3, "but not per keystroke")

    def test_a_save_that_changes_nothing_writes_nothing(self) -> None:
        type_text(self.buffer, "x")
        self.window._flush_save()
        before = self.window.store.get_note(self.first.id).modified_at
        self.window._content_changed()
        self.window._flush_save()
        self.assertEqual(self.window.store.get_note(self.first.id).modified_at, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
