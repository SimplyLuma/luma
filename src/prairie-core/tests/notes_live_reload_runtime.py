#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Notes: what sync writes to the library shows in an open window.

A real Notes window runs under Xvfb over a library of 40 pages. A second
connection to the same library stands in for luma-connect-sync and writes the
way it does (put_synced_note, as a pull takes another device's version).

1. A page changed and a page added elsewhere appear in the open window's list,
   which keeps its scroll position and the open page's row selected.
2. The open page, not edited here, shows the new version in place, and Notes
   writes nothing back for it.
3. The open page, being edited here when another version arrives, keeps the
   person's text; saving keeps the other version as exactly one
   "(other copy)", also over a failed and retried save and later saves.
4. No refresh loop: one change elsewhere is one refresh, Notes' own saves are
   none, and the library stays still afterwards.

All four run twice: with the library's folder watched (inotify), and with
watching made to fail, where Notes asks the library every few seconds. A test
says which way the first run went; it is skipped, loudly, only when this
machine cannot watch a folder at all at that moment.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("GSK_RENDERER", "cairo")
os.environ.setdefault("PRAIRIE_EDS_MODE", "disabled")
_home = tempfile.mkdtemp(prefix="notes-live-")
for variable, leaf in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                       ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
    os.environ[variable] = os.path.join(_home, leaf)
    os.makedirs(os.environ[variable], exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from prairie_apps import notes as notes_module  # noqa: E402
from prairie_apps.connect_notes import CONFLICT_SUFFIX  # noqa: E402
from prairie_apps.notes_backend import NotesStore  # noqa: E402

mock.patch("luma_appkit.widgets._prefers_dark", lambda: Adw.StyleManager.get_default().get_dark()).start()
context = GLib.MainContext.default()
PAGES = 40


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def pump_until(condition, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        pump(0.02)
    return condition()


class LiveReloadTests(unittest.TestCase):
    FORCE_POLL = False

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        if LiveReloadTests.app is None:  # one application for both runs
            LiveReloadTests.app = notes_module.NotesApplication()
            LiveReloadTests.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE | Gio.ApplicationFlags.HANDLES_OPEN)
            assert LiveReloadTests.app.register(None)
            notes_module.install_appkit()
            notes_module._install_notes_style()

    def setUp(self) -> None:
        data = tempfile.mkdtemp(prefix="library-", dir=_home)
        os.environ["XDG_DATA_HOME"] = data
        seed = NotesStore()
        self.ids = []
        for index in range(PAGES):
            note = seed.create_note(title=f"Page {index:02d}")
            seed.update_note(note.id, title=note.title, body=f"Text of page {index:02d}\nsecond line")
            self.ids.append(note.id)
        seed.close()
        if self.FORCE_POLL:
            refuse = GLib.Error("watching made to fail by the test")
            with mock.patch.object(Gio.File, "monitor_directory", side_effect=refuse):
                self.window = notes_module.NotesWindow(self.app)
        else:
            self.window = notes_module.NotesWindow(self.app)
        self.window.set_default_size(900, 520)
        self.window.present()
        pump(1.0)
        # "Sync": another connection to the same library, as luma-connect-sync has.
        self.sync = NotesStore(self.window.store.path)
        self.opened = self.window.current.id

    def tearDown(self) -> None:
        self.sync.close()
        self.window.close()
        pump(0.3)

    # -------------------------------------------------------------- helpers

    def take_from_elsewhere(self, note_id: str, title: str, body: str) -> None:
        """What a pull does with another device's version of a page."""
        before = self.sync.get_note(note_id)
        self.sync.put_synced_note(note_id, title=title, body=body, runs=(), folder_id=before.folder_id,
                                  folder_name="", favorite=before.favorite, created_at=before.created_at,
                                  modified_at="2026-09-22T12:00:00+00:00")

    def type_here(self, text: str) -> None:
        buffer = self.window.buffer
        buffer.insert(buffer.get_end_iter(), text)

    def editor_text(self) -> str:
        buffer = self.window.buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def copies(self) -> list:
        return [note for note in self.sync.list_notes() if note.title.endswith(CONFLICT_SUFFIX)]

    def refreshes(self) -> int:
        return getattr(self.window, "external_refreshes", 0)

    def load_count(self) -> list[int]:
        loads = [0]
        original = self.window.page.load

        def counted(*arguments, **keywords):
            loads[0] += 1
            return original(*arguments, **keywords)

        self.window.page.load = counted
        return loads

    # ---------------------------------------------------------------- tests

    def test_the_folder_is_watched_where_it_can_be(self) -> None:
        watch = getattr(self.window, "store_watch", "")
        print(f"\n  library changes seen by: {watch or 'nothing'}", file=sys.stderr)
        if self.FORCE_POLL:
            self.assertEqual(watch, "poll", "a folder that cannot be watched is asked instead")
            return
        if watch == "poll":
            try:
                Gio.File.new_for_path(_home).monitor_directory(Gio.FileMonitorFlags.NONE, None).cancel()
            except GLib.Error as error:
                self.skipTest(f"THIS MACHINE CANNOT WATCH A FOLDER NOW ({error.message}); "
                              "the watched path was not exercised in this run")
            self.fail("the folder could be watched, but Notes polled")
        self.assertEqual(watch, "monitor")

    def test_a_change_elsewhere_appears_in_the_open_window(self) -> None:
        adjustment = self.window.sidebar_list.get_ancestor(Gtk.ScrolledWindow).get_vadjustment()
        self.assertGreater(adjustment.get_upper() - adjustment.get_page_size(), 200,
                           "the list must be long enough to scroll for this to prove anything")
        adjustment.set_value(180)
        pump(0.3)
        scrolled = adjustment.get_value()
        self.assertEqual(scrolled, 180)
        target = self.ids[25]
        self.take_from_elsewhere(target, "Renamed on the phone", "Written on the phone")
        added = self.sync.create_note(title="Made on the web page")
        self.assertTrue(
            pump_until(lambda: self.window.note_rows.get(target) is not None
                       and self.window.note_rows[target].note.title == "Renamed on the phone"),
            "the page changed elsewhere shows its new title in the open window")
        pump_until(lambda: added.id in self.window.note_rows, 2.0)
        self.assertIn(added.id, self.window.note_rows, "a page added elsewhere appears")
        pump(0.5)
        self.assertEqual(adjustment.get_value(), scrolled, "the list keeps its scroll position")
        selected = self.window.sidebar_list.get_selected_row()
        self.assertIsNotNone(selected, "a row is still selected")
        self.assertEqual(selected.note.id, self.opened, "and it is the open page's row")

    def test_an_unedited_open_page_shows_the_new_version(self) -> None:
        stamp = self.sync.get_note(self.opened).modified_at
        self.take_from_elsewhere(self.opened, "Plan (phone)", "The phone's version\nof this page")
        self.assertTrue(pump_until(lambda: self.editor_text() == "The phone's version\nof this page"),
                        f"the open page shows the new version, not {self.editor_text()!r}")
        self.assertEqual(self.window.title_entry.get_text(), "Plan (phone)")
        self.assertNotEqual(self.sync.get_note(self.opened).modified_at, stamp)
        written = self.sync.get_note(self.opened).modified_at
        pump(2.0)  # well past the autosave delay
        self.assertEqual(self.sync.get_note(self.opened).modified_at, written,
                         "Notes writes nothing back for a page it only showed")
        self.assertEqual(self.copies(), [], "and keeps no copy")

    def test_an_edited_open_page_keeps_the_text_and_one_copy(self) -> None:
        original = self.editor_text()
        title = self.sync.get_note(self.opened).title
        # The first save fails once and is retried: still one copy.
        real_update = self.window.store.update_note
        failures = [0]

        def fail_once(*arguments, **keywords):
            if not failures[0]:
                failures[0] += 1
                raise sqlite3.OperationalError("disk I/O error")
            return real_update(*arguments, **keywords)

        with mock.patch.object(self.window.store, "update_note", fail_once):
            self.type_here(" and more typed here")
            typed = original + " and more typed here"
            self.take_from_elsewhere(self.opened, title, "The phone's version")
            pump(0.6)  # the change is usually noticed before the autosave writes
            self.assertEqual(self.editor_text(), typed, "the person's text stays in the editor")
            self.assertTrue(pump_until(lambda: self.sync.get_note(self.opened).body == typed, 6.0),
                            "the person's text is saved over the page")
        self.assertEqual(failures[0], 1, "the first save failed, as arranged")
        copies = self.copies()
        self.assertEqual([(note.title, note.body) for note in copies],
                         [(f"{title}{CONFLICT_SUFFIX}", "The phone's version")],
                         "the other version is kept as exactly one copy")
        self.type_here(" again")
        self.window._flush_save()
        pump(1.0)
        self.assertEqual(len(self.copies()), 1, "a later save makes no second copy")
        self.assertEqual(self.editor_text(), typed + " again")
        self.assertTrue(pump_until(lambda: copies[0].id in self.window.note_rows, 2.0),
                        "the copy is listed")

    def test_no_refresh_loop(self) -> None:
        loads = self.load_count()
        self.take_from_elsewhere(self.ids[30], "Changed once elsewhere", "once")
        self.assertTrue(pump_until(lambda: self.refreshes() == 1), "one change elsewhere, one refresh")
        pump(2.0)
        self.assertEqual(self.refreshes(), 1, "and no more after it")
        # Notes' own saves are not changes from elsewhere.
        for word in (" one", " two", " three"):
            self.type_here(word)
            self.window._flush_save()
            pump(0.4)
        pump(2.0)
        self.assertEqual(self.refreshes(), 1, "Notes' own saves never refresh the window")
        self.assertEqual(loads[0], 0, "the open page was never reloaded under the person")
        still = self.sync.get_note(self.opened).modified_at
        pump(1.5)
        self.assertEqual(self.sync.get_note(self.opened).modified_at, still, "the library is still")


class PollingTests(LiveReloadTests):
    """The same, where the library's folder cannot be watched."""
    FORCE_POLL = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
