# SPDX-License-Identifier: Apache-2.0
"""The save changes sheet, for Python applications.

An application that closes or quits with unsaved work calls `confirm_save`
with the facts: the documents, when each one's first unsaved edit happened,
whether one has never been saved, where its files usually go. The sheet itself
is the platform's, `LumaUI.SaveRequest` in libluma-ui, the same one C and C++
applications use, so every application asks the same question in the same
words with the same keys. This module only turns the facts into that call and
the answers back into `SaveResponse`.

The sheet belongs to the window that asked. It hangs from the bottom of that
window's title bar and dims only that window. Save (or Review changes… for
several documents) is the primary, Cancel sits under it, and the destructive
choice is set apart below as a small red text button. Enter takes the primary
unless focus is on another control, Escape cancels, and Ctrl+D is the one key
that discards.
"""

from __future__ import annotations

import enum
import gettext
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("LumaUI", "1")
from gi.repository import Adw, Gio, GLib, Gtk, LumaUI  # noqa: E402

from . import widgets  # noqa: E402

_ = gettext.translation("luma-appkit", fallback=True).gettext

#: The platform library this module drives. A mismatched install (a newer
#: kit over an older library) falls back to a plain alert rather than letting
#: a window close without asking.
NATIVE = hasattr(LumaUI, "SaveRequest")
STYLE_RESOURCE = "/org/projectluma/platform/luma-save-sheet.css"


class SaveChoice(enum.Enum):
    """What the person chose. The application acts on each one."""

    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"
    #: Several documents are resolved (reviewed, or discarded all): quit now.
    QUIT = "quit"


@dataclass
class UnsavedDocument:
    """The facts an application gives about one unsaved document."""

    name: str
    #: Seconds since the first edit that is not saved yet, as the application
    #: measured it, or None when it does not record that. The sheet never
    #: invents a number: without one it says the changes haven't been saved.
    unsaved_seconds: float | None = None
    #: Never saved anywhere: the sheet asks for a name and a place.
    never_saved: bool = False
    #: The window that shows this document; reviewing asks there.
    window: Gtk.Window | None = None
    #: The file extension a new file gets, such as ".layouts".
    extension: str = ""
    #: The application's own folders, as (label, folder), offered first.
    #: Documents and Desktop always follow, then Other location….
    places: Sequence[tuple[str, str]] = ()
    #: The application keeps a recovery copy until the person saves or discards.
    recovery_copy: bool = False
    #: What the document is called: "document", "project" or "note".
    kind: str = "document"


class SaveResponse:
    """One answer from the sheet.

    A SAVE response keeps the sheet up until the application calls `done()`
    (it saved) or `fail(message)` (it could not; the message appears on the
    sheet and the person can try again, pick another place, or cancel).
    """

    def __init__(self, choice: SaveChoice, document: UnsavedDocument | None = None,
                 destination: Gio.File | None = None,
                 documents: Sequence[UnsavedDocument] = (),
                 finished: Callable[[str | None], None] | None = None) -> None:
        self.choice = choice
        self.document = document
        #: For an untitled document, the file to write, in the chosen place.
        self.destination = destination
        #: For QUIT after Discard all, every document the quit leaves unsaved.
        self.documents = tuple(documents)
        self._finished = finished

    def done(self) -> None:
        finished, self._finished = self._finished, None
        if finished is not None:
            finished(None)

    def fail(self, message: str) -> None:
        finished, self._finished = self._finished, None
        if finished is not None:
            finished(message or "")


#: The sheet widget, for tests and for `AppWindow.presented_sheet`.
SaveSheet = LumaUI.SaveSheet if NATIVE else None

_style_ready = False
_requests: set = set()


def _install_style() -> None:
    """The kit keeps its own token sheets; add the sheet's, kept in step."""
    global _style_ready
    if _style_ready:
        return
    LumaUI.ui_tokens_provided_by_application()
    try:
        css = Gio.resources_lookup_data(STYLE_RESOURCE, Gio.ResourceLookupFlags.NONE).get_data().decode()
    except GLib.Error:
        css = ""
    if widgets.add_style_builder(lambda _appearance: css) is not None:
        _style_ready = True


def _native(document: UnsavedDocument):
    native = LumaUI.SaveDocument.new(document.name)
    native.set_never_saved(bool(document.never_saved))
    seconds = document.unsaved_seconds
    native.set_unsaved_seconds(-1.0 if seconds is None or seconds < 0 else float(seconds))
    if document.window is not None:
        native.set_window(document.window)
    native.set_extension(document.extension or "")
    for label, folder in document.places:
        native.add_place(str(label), str(Path(folder)))
    native.set_recovery_copy(bool(document.recovery_copy))
    native.set_kind(document.kind or "document")
    return native


def confirm_save(window: Gtk.Window, documents: Sequence[UnsavedDocument],
                 on_choice: Callable[[SaveResponse], None], *, quitting: bool = False):
    """Ask before closing or quitting with unsaved work.

    One document: `on_choice` receives SAVE (with `destination` set for an
    untitled document; call `done()` or `fail(message)` once written),
    DISCARD or CANCEL.

    Several (quitting): Review changes… asks for each document in turn, in its
    own window, sending SAVE or DISCARD for each; Cancel at any point sends
    CANCEL and stops the quit; when every document is answered, or after
    Discard all, `on_choice` receives QUIT.

    Returns the sheet now on screen, or None.
    """
    documents = list(documents)
    if not documents:
        on_choice(SaveResponse(SaveChoice.QUIT if quitting else SaveChoice.DISCARD))
        return None
    if not NATIVE:
        return _plain_alert(window, documents, on_choice, quitting)

    _install_style()
    natives = [_native(document) for document in documents]
    request = LumaUI.SaveRequest.new(natives, quitting)
    reviewed = [False]

    def document_for(native) -> UnsavedDocument | None:
        for candidate, document in zip(natives, documents):
            if candidate == native:
                return document
        return None

    def answered(_request, choice, native, destination) -> None:
        choice = SaveChoice(choice.value_nick)
        document = document_for(native) if native is not None else None
        if document is not None and len(documents) > 1:
            reviewed[0] = True
        if choice is SaveChoice.SAVE:
            response = SaveResponse(choice, document, destination, documents=documents,
                                    finished=lambda problem: request.finish_save(problem))
        elif choice is SaveChoice.QUIT:
            _requests.discard(request)
            response = SaveResponse(choice, None, documents=() if reviewed[0] else documents)
        else:
            if choice is SaveChoice.CANCEL or len(documents) == 1:
                _requests.discard(request)
            response = SaveResponse(choice, document, documents=documents)
        on_choice(response)

    request.connect("response", answered)
    _requests.add(request)
    request.present(window)
    return request.get_sheet()


def _plain_alert(window: Gtk.Window, documents: list[UnsavedDocument],
                 on_choice: Callable[[SaveResponse], None], quitting: bool):
    """Only for a kit installed over an older platform library."""
    document = documents[0]
    dialog = Adw.AlertDialog(heading=_("Save changes to “%s”?") % document.name,
                             body=_("Your changes haven’t been saved."))
    dialog.add_response("discard", _("Don’t save"))
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("save", _("Save"))
    dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("save")
    dialog.set_close_response("cancel")

    def chosen(_dialog, response: str) -> None:
        if response == "discard":
            on_choice(SaveResponse(SaveChoice.QUIT if len(documents) > 1 else SaveChoice.DISCARD,
                                   document, documents=documents))
        elif response == "save" and not document.never_saved and len(documents) == 1:
            on_choice(SaveResponse(SaveChoice.SAVE, document, documents=documents, finished=lambda _p: None))
        else:
            on_choice(SaveResponse(SaveChoice.CANCEL, document, documents=documents))

    dialog.connect("response", chosen)
    dialog.present(window)
    return None


__all__ = ["SaveChoice", "SaveResponse", "SaveSheet", "UnsavedDocument", "confirm_save"]
