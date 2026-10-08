# SPDX-License-Identifier: Apache-2.0
"""The interface Luma's shell reads to draw the stack itself.

An edge dock is a compositor's job, not an application's. On a session that
offers no way to ask for one — Mutter offers none — an ordinary window can only
approximate it, and every approximation has been worse than the last: a panel
that covered things, then a transparent window that was invisible and still
swallowed every click aimed near the screen edge.

So on Luma the shell draws the stack, and this is the seam. The application
still owns the notes, the store and the note windows; it publishes the list and
takes instructions. On any other desktop nothing claims the shell name, the
application draws its own dock as before, and none of this is reached.
"""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

#: Owned by Luma's shell while it is drawing the stack itself.
SHELL_NAME = "org.projectluma.Shell.StickyDock"

OBJECT_PATH = "/org/projectluma/StickyNotes"

INTERFACE = """
<node>
  <interface name='org.projectluma.StickyNotes.Dock'>
    <method name='ListNotes'>
      <arg type='a(sssbb)' name='notes' direction='out'/>
    </method>
    <method name='OpenNote'>
      <arg type='s' name='note_id' direction='in'/>
    </method>
    <method name='CreateNote'/>
    <signal name='NotesChanged'/>
  </interface>
</node>
"""


class DockInterface:
    """Publishes the stack, and accepts the two instructions it can act on."""

    def __init__(self, application, *, list_notes, open_note, create_note) -> None:
        self._application = application
        self._list_notes = list_notes
        self._open_note = open_note
        self._create_note = create_note
        self._registration = 0
        self._node = Gio.DBusNodeInfo.new_for_xml(INTERFACE)
        connection = application.get_dbus_connection()
        if connection is None:
            return
        self._connection = connection
        self._registration = connection.register_object(
            OBJECT_PATH, self._node.interfaces[0], self._call, None, None,
        )

    # -- Outgoing ---------------------------------------------------------

    def notes_changed(self) -> None:
        """Tell the shell the stack has moved under it."""
        if not self._registration:
            return
        try:
            self._connection.emit_signal(
                None, OBJECT_PATH, "org.projectluma.StickyNotes.Dock",
                "NotesChanged", None,
            )
        except GLib.Error:
            # A shell that has gone away is not this application's problem.
            pass

    # -- Incoming ---------------------------------------------------------

    def _call(self, _connection, _sender, _path, _interface, method, parameters,
              invocation) -> None:
        if method == "ListNotes":
            rows = [
                (note.id, note.tab_title, note.colour, note.pinned, note.completed)
                for note in self._list_notes()
            ]
            invocation.return_value(GLib.Variant("(a(sssbb))", (rows,)))
            return
        if method == "OpenNote":
            self._open_note(parameters.unpack()[0])
            invocation.return_value(None)
            return
        if method == "CreateNote":
            self._create_note()
            invocation.return_value(None)
            return
        invocation.return_error_literal(
            Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method,
        )


def shell_draws_the_dock(connection) -> bool:
    """Whether Luma's shell has claimed the stack.

    Asked once, at start-up, on the bus rather than by looking at the session
    type: the question is not "is this Luma" but "is something already drawing
    this", and only the name answers that.
    """

    if connection is None:
        return False
    try:
        reply = connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (SHELL_NAME,)), GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 2000, None,
        )
    except GLib.Error:
        return False
    return bool(reply.unpack()[0])
