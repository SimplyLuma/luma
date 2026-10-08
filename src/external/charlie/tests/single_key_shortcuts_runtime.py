#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""One-key shortcuts never fire while the person is typing.

Real key presses through Xvfb and XTest reach the real Charlie window: "e"
typed into the inline reply lands in the reply and does not archive the
conversation, Delete in an empty reply does not trash it, and the same keys
still archive and trash from the message list. The menu still shows E.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from pathlib import Path
import tempfile
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkX11", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from charlie_luma.application import CharlieApplication

x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
xtst = ctypes.CDLL(ctypes.util.find_library("Xtst"))
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XKeysymToKeycode.restype = ctypes.c_uint
x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
display = x11.XOpenDisplay(None)
assert display, "needs an X server (run under xvfb-run)"
context = GLib.MainContext.default()


def pump(seconds: float = .1) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(.005)


def wait(predicate, what: str) -> None:
    end = time.monotonic() + 15
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError(f"timed out: {what}")
        pump(.05)


def press(keysym: int) -> None:
    code = x11.XKeysymToKeycode(display, keysym)
    assert code
    for down in (1, 0):
        assert xtst.XTestFakeKeyEvent(display, code, down, 0)
        x11.XSync(display, False)
        pump(.08)


def menu_accel(model, action: str) -> str | None:
    for index in range(model.get_n_items()):
        for link in (Gio.MENU_LINK_SECTION, Gio.MENU_LINK_SUBMENU):
            child = model.get_item_link(index, link)
            if child is not None and (found := menu_accel(child, action)):
                return found
        name = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_ACTION, GLib.VariantType.new("s"))
        if name is not None and name.get_string() == action:
            accel = model.get_item_attribute_value(index, "accel", GLib.VariantType.new("s"))
            return accel.get_string() if accel else None
    return None


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


def field_text(widget) -> str:
    if isinstance(widget, Gtk.TextView):
        buffer = widget.get_buffer()
        return buffer.get_text(*buffer.get_bounds(), True)
    return widget.get_text()


def clear_field(widget) -> None:
    if isinstance(widget, Gtk.TextView):
        widget.get_buffer().set_text("")
    else:
        widget.set_text("")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="charlie-keys-") as directory:
        os.environ.update(XDG_DATA_HOME=directory, XDG_CONFIG_HOME=directory,
                          XDG_CACHE_HOME=directory)
        os.environ.update(LUMA_CHARLIE_FIXTURE="v71", LUMA_CHARLIE_OPEN="deck",
                          CHARLIE_TEST_WIDTH="500", CHARLIE_TEST_HEIGHT="828")
        application = CharlieApplication(data_home=Path(directory))
        application.did_initial_sync = True
        moves: list[str] = []
        failure: list[BaseException] = []

        def exercise() -> bool:
            try:
                window = application.window
                wait(lambda: window is not None and window.get_mapped() and window.thread is not None,
                     "demo conversation open")
                window._move = lambda _conversation, destination, _heading: moves.append(destination)
                assert set(window.guarded_shortcuts) == {"app.mail-archive", "app.mail-delete"}, window.guarded_shortcuts
                assert application.get_accels_for_action("app.mail-archive") == []
                assert menu_accel(application.get_menubar(), "app.mail-archive") == "e"
                x11.XSetInputFocus(display, window.get_surface().get_xid(), 2, 0)
                x11.XSync(display, False)
                pump(.3)

                # The shared v71 BarEntry is a real multiline GtkTextView;
                # earlier inline-reply implementations used GtkEditable.
                entries = [w for w in descendants(window.bar)
                           if isinstance(w, (Gtk.Editable, Gtk.TextView)) and w.get_mapped()]
                assert entries, "real phone quick-reply entry is missing"
                reply_entry = entries[0]
                reply_entry.grab_focus(); pump(.2)
                for keysym in (Gdk.KEY_e, Gdk.KEY_x, Gdk.KEY_e):
                    press(keysym)
                assert field_text(reply_entry) == "exe", field_text(reply_entry)
                assert moves == [], f"typing in the reply moved the conversation: {moves}"
                clear_field(reply_entry); pump(.1)
                press(Gdk.KEY_Delete)
                assert moves == [], f"Delete in an empty reply moved the conversation: {moves}"

                window._back_to_list(); pump(.2)
                # Phone navigation retains the open thread without forcing a
                # persistent selected-row highlight on its separate list.
                selected = window._rows.get(window.thread.id)
                assert selected is not None and selected.get_mapped(), "real mailbox row is missing"
                selected.grab_focus(); pump(.2)
                press(Gdk.KEY_e)
                assert moves == ["archive"], moves
                press(Gdk.KEY_Delete)
                assert moves == ["archive", "trash"], moves
            except BaseException as error:  # a failure must end the run, not hang it
                failure.append(error)
            application.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(600, exercise)
        status = application.run(["org.projectluma.Charlie"])
        if failure:
            raise failure[0]
        print("single-key shortcuts: typing is safe; list keys archive and trash")
        return status


if __name__ == "__main__":
    raise SystemExit(main())
