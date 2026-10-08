# SPDX-License-Identifier: Apache-2.0
"""Leaf starts, shows its library and keeps it correct when no folder can be watched.

Runs the real application under a display and a session bus, with every GIO
directory monitor refused the way an exhausted inotify instance limit refuses
it:

    dbus-run-session -- xvfb-run -a env PYTHONPATH=. python3 tests/runtime_monitor_refused.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

home = tempfile.TemporaryDirectory()
for variable, folder in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                         ("XDG_CONFIG_HOME", "config"), ("LEAF_BOOKS", "Books")):
    os.environ[variable] = str(Path(home.name) / folder)
    Path(os.environ[variable]).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("GTK_A11Y", "none")
os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from tests.fixtures import write_epub  # noqa: E402

books = Path(os.environ["LEAF_BOOKS"])
write_epub(books / "manners.epub")


def refuse(*_args, **_kwargs):
    raise GLib.Error.new_literal(Gio.io_error_quark(), "Too many open files", Gio.IOErrorEnum.TOO_MANY_OPEN_FILES)


def main() -> int:
    with mock.patch.object(Gio.File, "monitor_directory", side_effect=refuse) as monitor_directory:
        from luma_leaf.application import LeafApplication
        app = LeafApplication()
        app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
        failure: list[BaseException] = []
        steps = iter(("started", "added", "rescanned"))
        state = {"step": next(steps), "deadline": time.monotonic() + 30}

        def advance() -> bool:
            try:
                if time.monotonic() > state["deadline"]:
                    raise AssertionError(f"timed out at step {state['step']}: {len(app.library.books())} books")
                if state["step"] == "started":
                    if app.window is None or len(app.library.books()) != 1:
                        return GLib.SOURCE_CONTINUE
                    assert app.window.get_visible(), "the window is not shown"
                    assert app.window.stack.get_visible_child_name() == "library"
                    assert app.books_watch is not None and not app.books_watch.watching
                    assert app.library_watch is not None and not app.library_watch.watching
                    assert monitor_directory.call_count == 2, monitor_directory.call_args_list
                    assert (app.books_watch.path, app.library_watch.path) == (books, app.library.path.parent)
                    write_epub(books / "second.epub", identifier="urn:leaf:second-book", title="Second Thoughts")
                    state["step"] = next(steps)
                    return GLib.SOURCE_CONTINUE
                if state["step"] == "added":
                    # Showing the library looks again; nothing polls.
                    app.window.show_library()
                    state["step"] = next(steps)
                    return GLib.SOURCE_CONTINUE
                if len(app.library.books()) != 2:
                    return GLib.SOURCE_CONTINUE
                assert {book.title for book in app.library.books()} == {"A Test of Manners", "Second Thoughts"}
            except BaseException as error:  # a failure must end the run, not hang it
                failure.append(error)
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, advance)
        status = app.run([])
    if failure:
        raise failure[0]
    print("leaf: started and rescanned with every directory monitor refused")
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        home.cleanup()
