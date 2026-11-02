# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: remotes and gamepads, seen from an application.

In TV posture the shell turns every remote and gamepad into the TV's keys
(arrows, Return = OK, Escape = Back, Menu) before they reach any window, so
`TvFocus` and ordinary key handlers drive a TV presentation with no gamepad
code. A TV app (`[tv] presentation = "tv"`) gets those keys from a pad; any
other application gets the pad untouched, as on a desktop.

An app that reads gamepads itself (a game, an emulator's game view) claims
them while it does, so the shell sends no keys from a pad to its windows:

    claim_gamepads(True)    # entering the game
    claim_gamepads(False)   # back in the library: TV keys again

The claim lasts until it is released or the process leaves the session bus,
and is said again if the shell restarts. Home (a pad's Guide button, or
Select+Start) then stays with the app too. The shell side is
`org.projectluma.Shell.Remote1` (`ClaimGamepads(b)`, property `Gamepads`);
see docs/developer-platform/tv-mode.md ("Remotes and gamepads").
"""
from __future__ import annotations

from gi.repository import Gio, GLib

__all__ = ["claim_gamepads"]

BUS_NAME = "org.projectluma.Shell.Remote"
OBJECT_PATH = "/org/projectluma/Shell/Remote"
INTERFACE = "org.projectluma.Shell.Remote1"


class _Claim:
    def __init__(self) -> None:
        self.claimed = False
        self._owner: str | None = None
        self._bus: Gio.DBusConnection | None = None
        self._watch = Gio.bus_watch_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE, self._appeared, self._vanished)

    def _appeared(self, bus: Gio.DBusConnection, _name: str, owner: str) -> None:
        self._bus, self._owner = bus, owner
        if self.claimed:
            self._send()

    def _vanished(self, _bus, _name: str) -> None:
        self._owner = None

    def set(self, claimed: bool) -> None:
        if claimed == self.claimed:
            return
        self.claimed = claimed
        if self._owner is not None:
            self._send()

    def _send(self) -> None:
        self._bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, "ClaimGamepads",
                       GLib.Variant("(b)", (self.claimed,)), None, Gio.DBusCallFlags.NO_AUTO_START,
                       -1, None, self._done)

    def _done(self, bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error:
            pass  # no TV posture right now; said again when the shell appears


_claim: _Claim | None = None


def claim_gamepads(claimed: bool = True) -> None:
    """Read gamepads yourself (True) or let the shell's TV keys through again (False)."""
    global _claim
    if _claim is None:
        _claim = _Claim()
    _claim.set(bool(claimed))
