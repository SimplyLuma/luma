# SPDX-License-Identifier: Apache-2.0
"""Exercise real GIO file IO and GTK windows without a Notes database."""
import os
from pathlib import Path
import tempfile
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib
from prairie_apps.notes import NotesApplication, NotesWindow
from prairie_apps.notes_file import TextFileWindow
from prairie_apps.notes_document import TextDocument
from prairie_apps.notes_editor import NotesFormattingToolbar


def sheet_label(sheet, part):
    """The sheet's title or body label: the platform's getters (.76 on), or the
    attribute the Python sheet had before."""
    getter = getattr(sheet, f"get_{part}_label", None)
    return getter() if getter is not None else getattr(sheet, part)


def sheet_untitled(sheet):
    case = getattr(sheet, "get_case", None)
    return case() == "untitled" if case is not None else sheet.untitled


def wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for file-window operation")


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ["XDG_STATE_HOME"] = str(root / "state")
    app = NotesApplication()
    app.set_flags(Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    path = root / "README.md"
    path.write_bytes(b"# Read me\r\nOriginal text\r\n")
    path.chmod(0o640)
    file = Gio.File.new_for_path(str(path))
    app.do_open([file], 1, "")
    window = next(w for w in app.get_windows() if isinstance(w, TextFileWindow))
    wait(lambda: window.document is not None)
    assert window._text() == "# Read me\nOriginal text\n"
    assert isinstance(window.toolbar, NotesFormattingToolbar)
    assert not hasattr(window, "save_button"), "Save belongs in the app menu"
    assert window.file_title.get_label() == "README.md"
    assert app.lookup_action("file-save-as") is not None
    assert app.lookup_action("file-save") is not None
    assert app.get_accels_for_action("app.file-save")
    assert not hasattr(window, "store") and not hasattr(window, "sidebar_list")
    assert not any(isinstance(w, NotesWindow) for w in app.get_windows())
    assert not (root / "data").exists(), "File opening must not create a Notes library"
    for width in (360, 500, 1024, 1440):
        window.set_default_size(width, 600)
        try:
            wait(lambda: window.get_allocated_width() == width)
        except AssertionError:
            raise AssertionError(f"Requested {width}; allocated {window.get_allocated_width()}; content {window.get_width()}; default {window.get_default_size()}; editor {window.editor.get_width()}")
        assert window.editor.get_width() > 0
        assert window.editor.get_accessible_role() is not None
    app.do_open([file], 1, "")
    assert len([w for w in app.get_windows() if isinstance(w, TextFileWindow)]) == 1
    # The shared toolbar edits Markdown source and persists the formatting.
    window.buffer.set_text("Selected text")
    window.buffer.select_range(window.buffer.get_start_iter(), window.buffer.get_end_iter())
    window.format_buttons["bold"].emit("clicked")
    assert window._text() == "**Selected text**"
    window.save()
    wait(lambda: not window.saving)
    assert path.read_bytes() == b"**Selected text**"
    window.buffer.set_text("# Changed\nSaved from Notes\n")
    window.save()
    wait(lambda: not window.saving)
    assert path.read_bytes() == b"# Changed\r\nSaved from Notes\r\n"
    assert path.stat().st_mode & 0o777 == 0o640
    assert not window.buffer.get_modified()
    # A competing editor must not be overwritten; preserve the unsaved buffer.
    path.write_text("Changed elsewhere and longer\n")
    window.buffer.set_text("My unsaved edits\n")
    window.save()
    wait(lambda: not window.saving)
    assert path.read_text() == "Changed elsewhere and longer\n"
    assert window.buffer.get_modified()
    assert window._text() == "My unsaved edits\n"
    recovered = root / "Saved copy.txt"
    window._write(Gio.File.new_for_path(str(recovered)), None)
    wait(lambda: not window.saving)
    assert recovered.read_bytes() == b"My unsaved edits\r\n"
    assert path.read_text() == "Changed elsewhere and longer\n"
    assert not window.buffer.get_modified()
    window.buffer.set_text("Unsaved close test")
    # A second file has independent state and never becomes a note.
    second = root / "other.txt"
    second.write_text("Second file")
    app.do_open([Gio.File.new_for_path(str(second))], 1, "")
    other = next(w for w in app.get_windows() if isinstance(w, TextFileWindow) and w is not window)
    wait(lambda: other.document is not None)
    assert other._text() == "Second file"
    # Plain text formatting requires a distinct, explicitly selected Markdown
    # destination. Cancelling the picker must leave both file and buffer intact.
    original_save_as = other.save_as
    requested = []
    other.save_as = lambda **kwargs: requested.append(kwargs)
    other.buffer.select_range(other.buffer.get_start_iter(), other.buffer.get_end_iter())
    other.format_buttons["italic"].emit("clicked")
    assert requested == [{"markdown": True}]
    assert other._text() == "Second file" and second.read_text() == "Second file"
    target = root / "Formatted copy.md"
    class ChosenMarkdown:
        def save_finish(self, _result):
            return Gio.File.new_for_path(str(target))
    other._save_as_chosen(ChosenMarkdown(), None)
    wait(lambda: not other.saving)
    assert target.read_text() == "*Second file*"
    assert second.read_text() == "Second file"
    assert other.file_title.get_label() == "Formatted copy.md"
    other.save_as = original_save_as

    assert not (root / "data").exists()
    # Closing with changes asks with the kit's save sheet, on this window.
    from luma_appkit import SaveSheet
    window._close_file()
    sheet = window.presented_sheet
    assert isinstance(sheet, SaveSheet) and not sheet_untitled(sheet)
    assert sheet_label(sheet, "title").get_label() == f"Save changes to “{window.file.get_basename()}”?"
    assert "saved" in sheet_label(sheet, "body").get_label()
    sheet.activate_cancel()
    wait(lambda: window.presented_sheet is None)
    assert window.buffer.get_modified() and not window.closed
    window._close_file()
    window.presented_sheet.activate_discard()
    wait(lambda: window.closed)
    other.buffer.set_text("Save before close")
    other._close_file()
    other.presented_sheet.activate_primary()
    wait(lambda: other.closed)
    assert target.read_text() == "Save before close"
    assert second.read_text() == "Second file"
    app.quit()
    for raw in (b"\xef\xbb\xbfHello\r\n", "Hello\r\n".encode("utf-16"),
                b"\xfe\xff" + "Hello\r\n".encode("utf-16-be")):
        doc = TextDocument.decode(raw)
        assert doc.encode(doc.text) == raw
print("Notes file windows: real open/save, conflict protection, permissions, encodings, multiple files, widths, and library isolation PASS")
