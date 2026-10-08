# SPDX-License-Identifier: Apache-2.0
"""Asking the system for a file (v71 "File picker (system)").

The picker is the system's, not the application's: Filer's chooser, reached
through the file chooser portal, the same one every app gets. On a phone it
opens full screen over the app that asked, with who is asking and for what
above the place's crumbs ("Photos · Add photos", the app's icon), and a bar of
two rows (where you're looking, Search and Grid/List; then Cancel and Open
"name", Choose "folder", or a name field with Save). Cancel or a pick returns
to the app, which says what happened in its own toast.

An application never builds its own picker. It says what it is asking for, in
the words v71 uses, and the kit turns that into the portal's request: the
title is the ask, the picker starts in the right place, shows the right kind
of file, and its key says what will happen.

    request = FileRequest("Add photos", start="pictures", show="images", many=True)
    ask_for_file(window, request, lambda files: files and add(files), toast=toasts.show)

    FileRequest("Save a copy", mode="save", start="documents", name="Launch notes copy.md")
    FileRequest("Add a folder", mode="folder", start="music")      # Tide's library folder

`toast`, when given, gets v71's outcome line ("Photos opened “x.jpg”",
"Saved “x” to Documents", "Added 3 photos" is the app's own to say instead:
pass `outcome=`). Nothing is said on Cancel.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

__all__ = ["FILE_KINDS", "FILE_PLACES", "FileRequest", "ask_for_file", "file_dialog", "outcome_text"]

#: v71 FPFILT: what the picker shows ("Show" in its places panel).
FILE_KINDS = {
    "documents": ("Documents", ("text/*", "application/pdf", "application/vnd.oasis.opendocument.*",
                                "application/vnd.openxmlformats-officedocument.*", "application/msword",
                                "text/markdown")),
    "images": ("Images", ("image/*",)),
    "all": ("All files", ()),
}

#: v71's places (fpBarPhone PL) by name, the XDG folder each one is.
FILE_PLACES = {
    "home": None,
    "desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
    "documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
    "downloads": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
    "music": GLib.UserDirectory.DIRECTORY_MUSIC,
    "pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
    "videos": GLib.UserDirectory.DIRECTORY_VIDEOS,
}

MODES = ("open", "folder", "save")


@dataclass(frozen=True)
class FileRequest:
    """What an app asks the picker for. `ask` is v71's line under the app's name ("Add photos")."""

    ask: str
    mode: str = "open"                 # open, folder or save
    start: str | Path | None = None    # None: Downloads for save; picker default for open/import
    show: str = "all"                  # a FILE_KINDS key (open only)
    many: bool = False                 # open several (Photos' Add photos)
    name: str = ""                     # save: the name it suggests

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"a file request's mode is one of {', '.join(MODES)}")
        if self.show not in FILE_KINDS:
            raise ValueError(f"a file request shows one of {', '.join(FILE_KINDS)}")
        if isinstance(self.start, str) and self.start not in FILE_PLACES and not Path(self.start).is_absolute():
            raise ValueError(f"start is a folder or one of {', '.join(FILE_PLACES)}")

    @property
    def accept_label(self) -> str:
        """The key's word: Open, Choose or Save (the picker names the file or folder on a phone)."""
        return {"open": "Open", "folder": "Choose", "save": "Save"}[self.mode]

    def start_folder(self) -> Gio.File | None:
        if self.start is None:
            if self.mode != "save":
                return None
            path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            return Gio.File.new_for_path(path or str(Path(GLib.get_home_dir()) / "Downloads"))
        if isinstance(self.start, Path) or Path(str(self.start)).is_absolute():
            return Gio.File.new_for_path(str(self.start))
        special = FILE_PLACES[self.start]
        path = GLib.get_home_dir() if special is None else GLib.get_user_special_dir(special)
        return Gio.File.new_for_path(path) if path else None


def _filters(request: FileRequest) -> tuple[Gio.ListStore | None, Gtk.FileFilter | None]:
    if request.mode != "open" or request.show == "all":
        return None, None
    label, types = FILE_KINDS[request.show]
    chosen = Gtk.FileFilter(name=label)
    for mime in types:
        chosen.add_mime_type(mime)
    every = Gtk.FileFilter(name=FILE_KINDS["all"][0])
    every.add_pattern("*")
    store = Gio.ListStore(item_type=Gtk.FileFilter)
    store.append(chosen)
    store.append(every)
    return store, chosen


def file_dialog(request: FileRequest) -> Gtk.FileDialog:
    """The portal request for `request`: its title, key, first folder, filters and suggested name."""
    dialog = Gtk.FileDialog(title=request.ask, modal=True, accept_label=request.accept_label)
    folder = request.start_folder()
    if folder is not None:
        dialog.set_initial_folder(folder)
    filters, chosen = _filters(request)
    if filters is not None:
        dialog.set_filters(filters)
        dialog.set_default_filter(chosen)
    if request.mode == "save" and request.name:
        dialog.set_initial_name(request.name)
    return dialog


def _app_name() -> str:
    return GLib.get_application_name() or "The app"


def outcome_text(request: FileRequest, files: list[Gio.File]) -> str:
    """v71's toast once the picker returns (fpDone): opened, saved to a folder, or a folder chosen."""
    def name(f: Gio.File) -> str:
        return f.get_basename() or f.get_uri()

    if request.mode == "save":
        parent = files[0].get_parent()
        return f"Saved “{name(files[0])}” to {name(parent) if parent else 'your files'}"
    if request.mode == "folder":
        return f"{_app_name()} is adding “{name(files[0])}”"
    if len(files) == 1:
        return f"{_app_name()} opened “{name(files[0])}”"
    return f"{_app_name()} opened {len(files)} files"


def ask_for_file(window: Gtk.Window | None, request: FileRequest,
                 on_done: Callable[[list[Gio.File] | None], None], *,
                 toast: Callable[[str], object] | None = None,
                 outcome: Callable[[list[Gio.File]], str] | None = None) -> Gtk.FileDialog:
    """Ask the system picker; `on_done(files)` gets the picks, or None on Cancel.

    With `toast`, the outcome is said in the asking app when the picker returns (v71: the
    picker closes and the app's own toast confirms); `outcome` words it, else `outcome_text`."""
    dialog = file_dialog(request)

    def finished(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            if request.mode == "folder":
                picked = [dlg.select_folder_finish(result)]
            elif request.mode == "save":
                picked = [dlg.save_finish(result)]
            elif request.many:
                model = dlg.open_multiple_finish(result)
                picked = [model.get_item(i) for i in range(model.get_n_items())]
            else:
                picked = [dlg.open_finish(result)]
        except GLib.Error:   # Cancel (or the portal went away): back to the app, nothing said
            on_done(None)
            return
        picked = [f for f in picked if f is not None]
        if not picked:
            on_done(None)
            return
        on_done(picked)
        if toast is not None:
            toast(outcome(picked) if outcome is not None else outcome_text(request, picked))

    if request.mode == "folder":
        dialog.select_folder(window, None, finished)
    elif request.mode == "save":
        dialog.save(window, None, finished)
    elif request.many:
        dialog.open_multiple(window, None, finished)
    else:
        dialog.open(window, None, finished)
    return dialog
