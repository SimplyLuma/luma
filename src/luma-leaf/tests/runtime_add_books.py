# SPDX-License-Identifier: Apache-2.0
"""Exercise Leaf's review picker, EPUB import, drop path, and library return."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

private = tempfile.TemporaryDirectory(prefix="leaf-add-books-")
for name in ("DATA", "CONFIG", "CACHE", "STATE"):
    path = Path(private.name) / name.lower()
    path.mkdir()
    os.environ[f"XDG_{name}_HOME"] = str(path)
os.environ["LUMA_LEAF_PREVIEW"] = "1"
fixture = Path(__file__).resolve().parents[3] / "tests/fixtures/leaf-v70.json"
if not fixture.exists():
    fixture = Path(__file__).resolve().parents[1] / "fixtures/leaf-v70.json"
os.environ["LUMA_LEAF_FIXTURE"] = str(fixture)
os.environ["GTK_A11Y"] = "none"
os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from luma_leaf.application import LeafApplication  # noqa: E402


class FileDialog:
    opened = None

    def __init__(self, *, title):
        assert title == "Add books"
        self.callback = None
        FileDialog.opened = self

    def set_default_filter(self, selection):
        assert isinstance(selection, Gtk.FileFilter)

    def open_multiple(self, window, _cancellable, callback):
        assert window is not None
        self.callback = callback

    def open_multiple_finish(self, _result):
        files = Gio.ListStore.new(Gio.File)
        files.append(Gio.File.new_for_path(book_path))
        return files


def main() -> int:
    app = LeafApplication()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    failures: list[BaseException] = []
    state = {"ran": False}

    def check() -> bool:
        global book_path
        if app.window is None or state["ran"]:
            return GLib.SOURCE_CONTINUE
        state["ran"] = True
        try:
            library = app.library
            book_path = str(Path(private.name) / "picked.epub")
            Path(book_path).write_bytes(library._books["totc"].getvalue())
            assert len(library.books()) == 10
            app.window.library_view.emit("add-books")
            assert FileDialog.opened and FileDialog.opened.callback
            FileDialog.opened.callback(FileDialog.opened, None)
            assert len(library.books()) == 11
            assert app.window.reading
            app.window.show_library()
            assert app.window.reader.action_center.state == "hidden"
            assert app.window.stack.get_visible_child_name() == "library"
            file_list = Gdk.FileList.new_from_array([Gio.File.new_for_path(book_path)])
            assert app.window._drop_books(None, file_list, 0, 0)
            assert len(library.books()) == 11
            bad = Path(private.name) / "skip.txt"
            bad.write_text("not an EPUB")
            assert not app.window._drop_books(None, Gdk.FileList.new_from_array(
                [Gio.File.new_for_path(str(bad))]), 0, 0)
            assert not list(Path(os.environ["XDG_DATA_HOME"]).rglob("*.sqlite3"))
        except BaseException as error:
            failures.append(error)
        app.quit()
        return GLib.SOURCE_REMOVE

    with mock.patch.object(Gtk, "FileDialog", FileDialog):
        GLib.timeout_add(150, check)
        status = app.run([])
    private.cleanup()
    if failures:
        raise failures[0]
    assert state["ran"]
    print("leaf: picker, fixture import, drop, and library action bar PASS")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
