# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the idle view hook.

A TV app can leave something worth looking at from across the room. After a
delay without input it cross-fades from its presentation to an idle layer of
its own; any key brings the app back, and that key does nothing else.

    idle = TvIdle(focus, seconds=TV["idle"]["weather_seconds"],
                  on_idle=show_glance, on_wake=hide_glance)

`on_idle()` draws the idle layer and fades it in (an idle layer is its own
layer, never the app with its chrome hidden); `on_wake()` fades back. While a
text field has the focus, or the app holds `idle.hold()` (a sheet open, a
video playing full screen), the timer waits.

The person's choice wins over the app's delay: Settings › Idle & Sleep ›
"Show the idle view after" is the shell-state key `tv-idle-delay` (seconds;
-1 leaves it to the app, 0 is never) and is followed live.

Review: Shift+I forces idle; LUMA_TV_IDLE_MS sets the delay in milliseconds.
"""
from __future__ import annotations

import os
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .tv_focus import TvFocus  # noqa: E402

__all__ = ["TvIdle"]


class TvIdle:
    def __init__(self, focus: TvFocus, *, seconds: float, on_idle: Callable[[], None],
                 on_wake: Callable[[], None]) -> None:
        self.focus = focus
        override = os.environ.get("LUMA_TV_IDLE_MS", "").strip()
        self.app_delay_ms = int(override) if override.isdigit() else int(seconds * 1000)
        self._overridden = override.isdigit()
        self._state = _shell_state() if not self._overridden else None
        if self._state is not None:
            self._state.connect("changed::tv-idle-delay", lambda *_a: None if self.idle else self.poke())
        self.on_idle = on_idle
        self.on_wake = on_wake
        self.idle = False
        self._holds = 0
        self._source = 0
        focus.add_filter(self._key)
        # A TV has no pointer UI: only real movement wakes it (relayout sends motion at the same spot).
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._moved)
        self._pointer: tuple[float, float] | None = None
        self._motion = motion
        self._attached = None
        focus.root.connect("map", lambda *_a: self._attach())
        focus.root.connect("unmap", lambda *_a: self._stop())
        if focus.root.get_mapped():
            self._attach()

    def _attach(self) -> None:
        window = self.focus.root.get_root()
        if window is not None and window is not self._attached:
            window.add_controller(self._motion)
            self._attached = window
        self.poke()

    @property
    def delay_ms(self) -> int:
        """The delay in force: the person's choice, else the app's; 0 means never."""
        if self._state is not None:
            chosen = self._state.get_int("tv-idle-delay")
            if chosen >= 0:
                return chosen * 1000
        return self.app_delay_ms

    POINTER_SLOP = 24.0

    def _moved(self, _controller, x: float, y: float) -> None:
        last, self._pointer = self._pointer, (x, y)
        if last is not None and abs(x - last[0]) + abs(y - last[1]) > self.POINTER_SLOP:
            self.poke()

    def hold(self) -> Callable[[], None]:
        """Keep the app awake until the returned function is called."""
        self._holds += 1
        self._stop()
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self._holds -= 1
                self.poke()
        return release

    def poke(self) -> None:
        """Input happened: restart the delay (and wake, if idle)."""
        if self.idle:
            self.wake()
            return
        self._stop()
        delay = self.delay_ms
        if self._holds or delay <= 0 or not self.focus.root.get_mapped():
            return
        self._source = GLib.timeout_add(delay, self._fire)

    def go_idle(self) -> None:
        self._stop()
        if not self.idle:
            self.idle = True
            self.on_idle()

    def wake(self) -> None:
        if self.idle:
            self.idle = False
            self.on_wake()
        self.poke()

    def _fire(self) -> bool:
        self._source = 0
        if self._typing():
            self.poke()
            return False
        self.go_idle()
        return False

    def _stop(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0

    def _typing(self) -> bool:
        window = self.focus.root.get_root()
        widget = window.get_focus() if window is not None else None
        return isinstance(widget, (Gtk.Text, Gtk.Entry)) or self.focus.grabbed

    def _key(self, keyval: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                      Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R):
            return False
        if self.idle:
            self.wake()
            return True          # the waking key does nothing else
        if keyval == Gdk.KEY_I and state & Gdk.ModifierType.SHIFT_MASK and not self._typing():
            self.go_idle()
            return True
        self.poke()
        return False


def _shell_state():
    """The shell-state settings when installed with the idle key, else None."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.project_luma.shell-state", True) if source else None
    if schema is None or not schema.has_key("tv-idle-delay"):
        return None
    return Gio.Settings.new("org.project_luma.shell-state")
