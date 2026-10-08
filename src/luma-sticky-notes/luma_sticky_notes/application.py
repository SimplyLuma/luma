# SPDX-License-Identifier: Apache-2.0
"""Sticky Notes: a dock of notes against the screen edge, and the notes."""

from __future__ import annotations

import os
import pathlib
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from luma_appkit import add_style_sheet, install_appkit  # noqa: E402

from .dock import StickyDock
from .note_window import NoteWindow
from .shell_dock import DockInterface, shell_draws_the_dock
from .store import StickyNotesStore

APP_ID = "org.projectluma.StickyNotes"
STYLE_SHEET = "sticky-notes.css"


class StickyNotesApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.store: StickyNotesStore | None = None
        self.dock: StickyDock | None = None
        self.shell_dock: DockInterface | None = None
        self._shell_draws_the_dock = False
        self._notes: dict[str, NoteWindow] = {}

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        _install_style()
        self.store = StickyNotesStore()
        # The trash keeps a deleted note for thirty days; the window it is
        # kept for is enforced here, once, rather than by a resident timer.
        self.store.purge_expired()
        # An edge dock belongs to the compositor. Where the session draws one
        # for us, this application publishes the stack and stays out of the way;
        # everywhere else it draws its own, exactly as before.
        self._shell_draws_the_dock = shell_draws_the_dock(self.get_dbus_connection())
        self.shell_dock = DockInterface(
            self,
            list_notes=lambda: self.store.list_notes(),
            open_note=self._open_by_id,
            create_note=self._create_from_shell,
        )

    def _open_by_id(self, note_id: str) -> None:
        try:
            note = self.store.get_note(note_id)
        except Exception:  # noqa: BLE001 - a stale id from the shell is not fatal
            return
        self.open_note(note)

    def _create_from_shell(self) -> None:
        note = self.store.create_note()
        if self.dock is not None:
            self.dock.reload()
        if self.shell_dock is not None:
            self.shell_dock.notes_changed()
        self.open_note(note)

    def do_activate(self) -> None:
        if self._shell_draws_the_dock:
            # The stack is on screen already. Activation here means somebody
            # asked for Sticky Notes with no note in mind, so give them a new
            # one rather than a second stack.
            self._create_from_shell()
            return
        if self.dock is None:
            self.dock = StickyDock(
                application=self, store=self.store, open_note=self.open_note
            )
            self.dock.connect("close-request", self._dock_closed)
        self.dock.present()

    def _dock_closed(self, _window) -> bool:
        # Closing the dock closes Sticky Notes. The notes themselves are
        # already saved, so there is nothing to ask about.
        self.quit()
        return False

    def open_note(self, note) -> None:
        window = self._notes.get(note.id)
        if window is None:
            window = NoteWindow(
                application=self,
                store=self.store,
                note=note,
                on_changed=self._note_changed,
                on_deleted=self._note_deleted,
            )
            window.connect("close-request", self._note_closed)
            self._notes[note.id] = window
        window.present()

    def _note_changed(self, note) -> None:
        if self.dock is not None:
            self.dock.note_changed(note)
        if self.shell_dock is not None:
            self.shell_dock.notes_changed()

    def _note_deleted(self, note) -> None:
        if self.dock is not None:
            self.dock.note_deleted(note)
        if self.shell_dock is not None:
            self.shell_dock.notes_changed()

    def _note_closed(self, window) -> bool:
        self._notes.pop(window.note.id, None)
        return False

    def do_shutdown(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None
        Adw.Application.do_shutdown(self)


def _install_style() -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    for candidate in (
        os.environ.get("LUMA_STICKY_NOTES_STYLE_PATH", ""),
        str(pathlib.Path(__file__).resolve().parent.parent / "data" / STYLE_SHEET),
        f"/usr/share/luma-sticky-notes/{STYLE_SHEET}",
    ):
        if candidate and pathlib.Path(candidate).is_file():
            # The kit owns this sheet, so it follows the surface treatment.
            add_style_sheet(candidate)
            return


def main(argv: list[str] | None = None) -> int:
    return StickyNotesApplication().run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
