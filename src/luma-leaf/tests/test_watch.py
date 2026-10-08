# SPDX-License-Identifier: Apache-2.0
"""Leaf keeps its library correct when a folder cannot be watched."""
import os
import pathlib
import re
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from tests.fixtures import write_epub


def _refuse(*_args, **_kwargs):
    # What GIO raises when inotify has run out of instances.
    raise GLib.Error.new_literal(Gio.io_error_quark(), "Too many open files", Gio.IOErrorEnum.TOO_MANY_OPEN_FILES)


class FolderWatchWithoutMonitor(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.environ["XDG_CACHE_HOME"] = self.directory.name
        from luma_leaf import importer, store, watch
        self.importer, self.watch = importer, watch
        self.library = store.Library(pathlib.Path(self.directory.name) / "library.sqlite3")
        self.books = pathlib.Path(self.directory.name) / "Books"
        self.books.mkdir()
        write_epub(self.books / "manners.epub")
        self.importer.scan(self.library, self.books)
        self.changes = 0

    def tearDown(self):
        self.directory.cleanup()

    def _rescan(self):
        self.changes += 1
        self.importer.scan(self.library, self.books)

    def test_a_refused_monitor_warns_once_and_rescans_on_the_next_look(self):
        with mock.patch.object(Gio.File, "monitor_directory", side_effect=_refuse) as monitor_directory:
            with self.assertLogs("leaf", level="WARNING") as logged:
                folder = self.watch.FolderWatch(self.books, self._rescan, recursive=True,
                                                flags=Gio.FileMonitorFlags.WATCH_MOVES)
        monitor_directory.assert_called_once()
        self.assertFalse(folder.watching)
        self.assertEqual(len(logged.records), 1)
        self.assertIn(str(self.books), logged.output[0])
        self.assertEqual(len(self.library.books()), 1)

        # Nothing changed: no rescan, and no second warning.
        with self.assertNoLogs("leaf", level="WARNING"):
            self.assertFalse(folder.check())
        self.assertEqual(self.changes, 0)

        # A book arrives in a subfolder while nothing watches.
        (self.books / "later").mkdir()
        write_epub(self.books / "later" / "second.epub", identifier="urn:leaf:second-book", title="Second Thoughts")
        self.assertTrue(folder.check())
        self.assertEqual(self.changes, 1)
        self.assertEqual(len(self.library.books()), 2)
        self.assertFalse(folder.check())

        # And one leaves: the importer marks it missing, never removes it.
        (self.books / "manners.epub").unlink()
        self.assertTrue(folder.check())
        self.assertEqual(sorted(book.missing for book in self.library.books()), [False, True])

    def test_hidden_folders_are_not_looked_at(self):
        with mock.patch.object(Gio.File, "monitor_directory", side_effect=_refuse), self.assertLogs("leaf"):
            folder = self.watch.FolderWatch(self.books, self._rescan, recursive=True)
        (self.books / ".partial").mkdir()
        (self.books / ".partial" / "download.epub").write_bytes(b"not yet")
        # The hidden folder itself changes the listing once; what is inside it never does.
        folder.check()
        (self.books / ".partial" / "download.epub").write_bytes(b"still downloading")
        self.assertFalse(folder.check())

    def test_a_flat_watch_sees_the_library_file_change(self):
        library_file = self.library.path
        with mock.patch.object(Gio.File, "monitor_directory", side_effect=_refuse), self.assertLogs("leaf"):
            folder = self.watch.FolderWatch(library_file.parent, self._rescan)
        self.assertFalse(folder.check())
        self.library.set_shelf(self.library.books()[0].id, "finished")
        os.utime(library_file, ns=(0, library_file.stat().st_mtime_ns + 1_000_000_000))
        self.assertTrue(folder.check())

    def test_a_missing_folder_is_not_an_error(self):
        with mock.patch.object(Gio.File, "monitor_directory", side_effect=_refuse), self.assertLogs("leaf"):
            folder = self.watch.FolderWatch(self.books / "absent", self._rescan, recursive=True)
        self.assertFalse(folder.check())


class FolderWatchWithMonitor(unittest.TestCase):
    def test_a_working_monitor_never_lists_the_folder(self):
        from luma_leaf import watch
        with tempfile.TemporaryDirectory() as directory:
            changes = []
            with self.assertNoLogs("leaf", level="WARNING"):
                folder = watch.FolderWatch(pathlib.Path(directory), lambda: changes.append(1))
            self.assertTrue(folder.watching)
            self.assertIsInstance(folder.monitor, Gio.FileMonitor)
            with mock.patch.object(watch.FolderWatch, "_take_fingerprint") as fingerprint:
                self.assertFalse(folder.check())
            fingerprint.assert_not_called()
            folder.monitor.emit("changed", Gio.File.new_for_path(directory), None, Gio.FileMonitorEvent.CREATED)
            self.assertEqual(changes, [1])
            folder.cancel()


class ApplicationUsesTheWatch(unittest.TestCase):
    """The application creates no monitor of its own and looks again on activation."""

    source = (pathlib.Path(__file__).resolve().parents[1] / "luma_leaf" / "application.py").read_text()

    def test_no_direct_file_monitors(self):
        for package_file in (pathlib.Path(__file__).resolve().parents[1] / "luma_leaf").glob("*.py"):
            if package_file.name == "watch.py":
                continue
            self.assertIsNone(re.search(r"\.monitor(_directory|_file)?\(", package_file.read_text()), package_file.name)

    def test_both_folders_use_folder_watch(self):
        self.assertIn("FolderWatch(self.library.path.parent, self._library_file_changed)", self.source)
        self.assertRegex(self.source, r"FolderWatch\(folder, self\._folder_changed")

    def test_activation_and_the_library_look_again(self):
        self.assertIn('"notify::is-active"', self.source)
        show_library = self.source.split("def show_library", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("check_folders()", show_library)


if __name__ == "__main__":
    unittest.main()
