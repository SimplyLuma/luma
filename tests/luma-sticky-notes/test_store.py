# SPDX-License-Identifier: Apache-2.0
"""The sticky-note store, on its own terms: no display, no toolkit, no GTK."""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from luma_sticky_notes.store import (
    COLOURS,
    SCHEMA_VERSION,
    StickyNotesStore,
    data_directory,
)


def _store_source() -> str:
    """The store's own file, found through the import rather than the checkout.

    These tests also run inside an RPM build root, where the package is on the
    path but the source tree is not where a checkout would put it. Asking the
    imported module where it lives is true in both places.
    """
    import luma_sticky_notes.store as module

    return Path(module.__file__).read_text(encoding="utf-8")


class StickyNotesStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "sticky-notes" / "notes.sqlite3"
        self.store = StickyNotesStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _reopen(self) -> None:
        self.store.close()
        self.store = StickyNotesStore(self.path)

    # ── The note's life ──────────────────────────────────────────────────

    def test_a_note_is_created_named_recoloured_completed_and_deleted(self) -> None:
        note = self.store.create_note(title="Groceries")
        self.assertEqual(note.title, "Groceries")
        self.assertEqual(note.tab_title, "GROCERIES")
        self.assertEqual(note.colour, "yellow")
        self.assertFalse(note.pinned)
        self.assertFalse(note.completed)

        note = self.store.update_note(note.id, title="Groceries", body="Oats\nSalt")
        self.assertEqual(note.body, "Oats\nSalt")

        note = self.store.rename_note(note.id, "  Office   run  ")
        self.assertEqual(note.title, "Office run")

        note = self.store.set_colour(note.id, "purple")
        self.assertEqual(note.colour, "purple")

        note = self.store.set_pinned(note.id, True)
        self.assertTrue(note.pinned)

        note = self.store.set_complete(note.id, True)
        self.assertTrue(note.completed)
        self.assertIsNotNone(note.completed_at)

        self._reopen()
        stored = self.store.get_note(note.id)
        self.assertEqual(
            (stored.title, stored.body, stored.colour, stored.pinned, stored.completed),
            ("Office run", "Oats\nSalt", "purple", True, True),
        )

    def test_completion_is_a_state_and_never_removes_the_note(self) -> None:
        note = self.store.create_note(title="Ship it")
        self.store.update_note(note.id, title="Ship it", body="the release")
        self.store.set_complete(note.id, True)
        self.assertEqual(len(self.store.list_notes()), 1)
        self.assertEqual(self.store.list_notes(include_completed=False), ())
        self.assertEqual(self.store.get_note(note.id).body, "the release")

        reopened = self.store.set_complete(note.id, False)
        self.assertFalse(reopened.completed)
        self.assertIsNone(reopened.completed_at)
        self.assertEqual(len(self.store.list_notes(include_completed=False)), 1)

    def test_delete_is_recoverable_and_the_trash_expires_on_its_own(self) -> None:
        keep = self.store.create_note(title="Keep")
        gone = self.store.create_note(title="Gone")
        self.store.update_note(gone.id, title="Gone", body="still here really")

        self.store.delete_note(gone.id)
        self.assertEqual([note.id for note in self.store.list_notes()], [keep.id])
        self.assertEqual([note.id for note in self.store.list_notes(deleted=True)], [gone.id])
        # Deleting is not losing: the text is untouched in the trash.
        self.assertEqual(self.store.get_note(gone.id).body, "still here really")

        restored = self.store.restore_note(gone.id)
        self.assertIsNone(restored.deleted_at)
        self.assertEqual(len(self.store.list_notes()), 2)

        # A note deleted long enough ago is purged; a fresh one is not.
        self.store.delete_note(gone.id)
        stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(
            timespec="microseconds"
        )
        with self.store.transaction() as database:
            database.execute(
                "UPDATE notes SET deleted_at = ? WHERE id = ?", (stale, gone.id)
            )
        self.store.delete_note(keep.id)
        self.assertEqual(self.store.purge_expired(), 1)
        self.assertEqual([note.id for note in self.store.list_notes(deleted=True)], [keep.id])
        with self.assertRaises(KeyError):
            self.store.get_note(gone.id)

    def test_only_a_trashed_note_can_be_purged(self) -> None:
        note = self.store.create_note(title="Live")
        with self.assertRaises(KeyError):
            self.store.purge_note(note.id)
        self.store.delete_note(note.id)
        self.store.purge_note(note.id)
        with self.assertRaises(KeyError):
            self.store.get_note(note.id)

    def test_a_deleted_note_cannot_be_edited_by_a_stale_window(self) -> None:
        note = self.store.create_note(title="Stale")
        self.store.delete_note(note.id)
        for call in (
            lambda: self.store.update_note(note.id, title="x", body="y"),
            lambda: self.store.set_colour(note.id, "blue"),
            lambda: self.store.set_pinned(note.id, True),
            lambda: self.store.set_complete(note.id, True),
            lambda: self.store.delete_note(note.id),
        ):
            with self.assertRaises(KeyError):
                call()

    # ── Order ────────────────────────────────────────────────────────────

    def test_the_stack_is_pinned_first_and_editing_never_moves_a_tab(self) -> None:
        first = self.store.create_note(title="First")
        second = self.store.create_note(title="Second")
        third = self.store.create_note(title="Third")
        self.assertEqual(
            [note.id for note in self.store.list_notes()],
            [first.id, second.id, third.id],
        )
        self.store.update_note(first.id, title="First", body="edited")
        self.assertEqual(
            [note.id for note in self.store.list_notes()],
            [first.id, second.id, third.id],
        )
        self.store.set_pinned(third.id, True)
        self.assertEqual(
            [note.id for note in self.store.list_notes()],
            [third.id, first.id, second.id],
        )
        self._reopen()
        self.assertEqual(
            [note.id for note in self.store.list_notes()],
            [third.id, first.id, second.id],
        )

    # ── What the store refuses ───────────────────────────────────────────

    def test_a_colour_outside_the_palette_is_refused_by_python_and_by_sqlite(self) -> None:
        note = self.store.create_note()
        with self.assertRaises(ValueError):
            self.store.set_colour(note.id, "chartreuse")
        with self.assertRaises(ValueError):
            self.store.create_note(colour="chartreuse")
        self.assertEqual(self.store.get_note(note.id).colour, "yellow")
        # The same rule is in the schema, so a writer that bypasses the API
        # cannot put a colour in the file that the application cannot draw.
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as database:
                database.execute(
                    "UPDATE notes SET colour = ? WHERE id = ?", ("chartreuse", note.id)
                )

    def test_every_palette_colour_is_accepted(self) -> None:
        note = self.store.create_note()
        for colour in COLOURS:
            self.assertEqual(self.store.set_colour(note.id, colour).colour, colour)

    def test_a_title_is_one_clean_bounded_line(self) -> None:
        note = self.store.create_note(title="a" * 400)
        self.assertEqual(len(note.title), 120)
        note = self.store.rename_note(note.id, "two\nlines\x00here")
        self.assertEqual(note.title, "two lines here")
        note = self.store.rename_note(note.id, "   ")
        self.assertEqual(note.title, "")
        self.assertEqual(note.display_title, "Untitled note")
        self.assertEqual(note.tab_title, "NOTE")

    def test_a_body_keeps_its_line_breaks_and_loses_nothing_else(self) -> None:
        note = self.store.create_note(title="Shape")
        note = self.store.update_note(
            note.id, title="Shape", body="one\ntwo\tthree\x00four\x07five"
        )
        # Line breaks and tabs are the note's own shape and survive; a stray
        # control character becomes a space so the words either side stay apart.
        self.assertEqual(note.body, "one\ntwo\tthree four five")

    def test_an_oversized_body_is_refused_without_a_partial_write(self) -> None:
        note = self.store.create_note(title="Bounded")
        self.store.update_note(note.id, title="Bounded", body="safe")
        with self.assertRaises(ValueError):
            self.store.update_note(note.id, title="Bounded", body="x" * (64 * 1024 + 1))
        self.assertEqual(self.store.get_note(note.id).body, "safe")

    def test_an_unknown_note_is_a_key_error_not_a_silent_nothing(self) -> None:
        for call in (
            lambda: self.store.get_note("no-such-note"),
            lambda: self.store.rename_note("no-such-note", "x"),
            lambda: self.store.restore_note("no-such-note"),
            lambda: self.store.purge_note("no-such-note"),
        ):
            with self.assertRaises(KeyError):
                call()

    # ── Migration ────────────────────────────────────────────────────────

    def test_the_schema_is_stamped_once_and_reopening_is_not_a_migration(self) -> None:
        note = self.store.create_note(title="Durable")
        with sqlite3.connect(self.path) as probe:
            self.assertEqual(
                int(probe.execute("PRAGMA user_version").fetchone()[0]), SCHEMA_VERSION
            )
        probe.close()
        self._reopen()
        self.assertEqual(self.store.get_note(note.id).title, "Durable")
        with sqlite3.connect(self.path) as probe:
            self.assertEqual(
                int(probe.execute("PRAGMA user_version").fetchone()[0]), SCHEMA_VERSION
            )
        probe.close()

    def test_a_file_from_a_newer_release_is_refused_rather_than_rewritten(self) -> None:
        self.store.create_note(title="From the future")
        self.store.close()
        with sqlite3.connect(self.path) as probe:
            probe.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        probe.close()
        with self.assertRaises(RuntimeError):
            StickyNotesStore(self.path)
        # Refusing has to leave the file alone, so an older release cannot
        # destroy notes a newer one wrote.
        with sqlite3.connect(self.path) as probe:
            self.assertEqual(
                int(probe.execute("SELECT COUNT(*) FROM notes").fetchone()[0]), 1
            )
        probe.close()
        self.store = StickyNotesStore(self.root / "replacement.sqlite3")

    # ── Privacy ──────────────────────────────────────────────────────────

    def test_the_data_directory_and_the_database_are_private(self) -> None:
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_a_directory_left_open_by_an_older_release_is_made_private(self) -> None:
        loose = self.root / "loose"
        loose.mkdir(mode=0o755)
        self.assertEqual(stat.S_IMODE(loose.stat().st_mode), 0o755)
        store = StickyNotesStore(loose / "notes.sqlite3")
        try:
            self.assertEqual(stat.S_IMODE(loose.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((loose / "notes.sqlite3").stat().st_mode), 0o600)
        finally:
            store.close()

    def test_the_default_location_follows_the_data_home(self) -> None:
        self.assertEqual(
            data_directory({"XDG_DATA_HOME": "/data/home"}),
            Path("/data/home/luma/sticky-notes"),
        )
        self.assertEqual(
            data_directory({"HOME": "/home/person"}),
            Path("/home/person/.local/share/luma/sticky-notes"),
        )
        # An empty XDG_DATA_HOME is unset, per the base directory specification.
        self.assertEqual(
            data_directory({"HOME": "/home/person", "XDG_DATA_HOME": ""}),
            Path("/home/person/.local/share/luma/sticky-notes"),
        )

    def test_the_store_never_writes_note_text_anywhere_but_its_own_file(self) -> None:
        """A note's words are not a diagnostic. The module cannot log at all."""
        source = _store_source()
        for forbidden in ("import logging", "logging.", "print(", "sys.stderr", "warnings."):
            self.assertNotIn(forbidden, source, forbidden)

    def test_the_store_needs_no_display(self) -> None:
        """The store is importable with no session of any kind around it.

        These tests already prove it by running: nothing here sets DISPLAY or
        WAYLAND_DISPLAY. This states the boundary that keeps it true — the
        store must not reach the toolkit, directly or by import.
        """
        self.assertIsNone(os.environ.get("DISPLAY"))
        self.assertIsNone(os.environ.get("WAYLAND_DISPLAY"))
        import luma_sticky_notes.store as module

        self.assertEqual(
            [name for name in dir(module) if name in {"gi", "Gtk", "Adw", "Gdk", "GLib"}],
            [],
        )
        source = _store_source()
        self.assertNotIn("import gi", source)


if __name__ == "__main__":
    unittest.main()
