# SPDX-License-Identifier: Apache-2.0
"""The session lock, as the screen shield reports it.

No picker is shown and no share is started while the session is locked: a
request that arrives then waits, unanswered, and goes on after unlock in the
normal session, where the person can still deny it. The lock is read from
org.gnome.ScreenSaver, which the Shell (and Phosh) implement: Active is true
from the moment the lock screen is up until it has gone after unlock.

Until the first answer arrives, and wherever nothing implements the
interface, the session counts as open. That is safe: the Shell holds its own
picker while it is locked, and Mutter refuses to start a share then, which
the screen sharing backend treats as "wait for unlock".
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

LOGGER = logging.getLogger("luma-portal")

SCREENSAVER_NAME = "org.gnome.ScreenSaver"
SCREENSAVER_PATH = "/org/gnome/ScreenSaver"
SCREENSAVER_IFACE = "org.gnome.ScreenSaver"


class SessionLock:
    """Whether the session is locked, and what waits for it to open."""

    def __init__(self, connection) -> None:
        self._connection = connection
        self.locked = False
        self._waiting: list = []
        connection.signal_subscribe(
            SCREENSAVER_NAME, SCREENSAVER_IFACE, "ActiveChanged", SCREENSAVER_PATH,
            None, Gio.DBusSignalFlags.NONE, self._on_active_changed, None)
        self.refresh()

    def refresh(self) -> None:
        """Ask the screen shield again (once at start, and when Mutter says it is locked).

        The call may start the service: org.gnome.ScreenSaver is the Shell's
        D-Bus-activated proxy, and its signals reach us only once it runs.
        """
        self._connection.call(
            SCREENSAVER_NAME, SCREENSAVER_PATH, SCREENSAVER_IFACE, "GetActive",
            None, GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1,
            None, self._on_get_active, None)

    def _on_get_active(self, source, result, _data=None) -> None:
        try:
            (active,) = source.call_finish(result).unpack()
        except GLib.Error as error:
            LOGGER.debug("no screen shield to ask: %s", error.message)
            return
        self._set(bool(active))

    def _on_active_changed(self, _c, _s, _p, _i, _signal, parameters, _d) -> None:
        (active,) = parameters.unpack()
        self._set(bool(active))

    def _set(self, locked: bool) -> None:
        if locked == self.locked:
            return
        self.locked = locked
        LOGGER.info("the session is %s", "locked" if locked else "unlocked")
        if locked:
            return
        waiting, self._waiting = self._waiting, []
        for callback in waiting:
            callback()

    def when_unlocked(self, callback) -> None:
        """Call callback now if the session is open, else once it opens."""
        if not self.locked:
            callback()
        elif callback not in self._waiting:
            self._waiting.append(callback)
