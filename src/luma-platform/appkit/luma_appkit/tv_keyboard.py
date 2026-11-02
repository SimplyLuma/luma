# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the system keyboard, seen from an application.

There is one on-screen keyboard: in TV posture the shell's Keycap keyboard
docks to the bottom for whatever text field has the focus, the remote moves
over its keys and a real keyboard types straight into the field. An app uses
ordinary GTK text widgets (a `TvField`'s `Gtk.Text`, a `Gtk.Entry`); while
the keyboard is open the shell takes the remote's arrows, OK and Back, and
gives them back when it closes, so `TvFocus` needs nothing special.

Two things are the app's to say or know:

    set_keyboard_action(field.text, _("Search"))   # the blue action key's label
    stop = watch_tv_keyboard(lambda visible, height: ...)   # height in TV units

The action key sends Enter to the field (`activate`, or `TvFocus`'s
`on_activate` for an editable item) and puts the keyboard away. Without a
label it reads Go for an address field (`Gtk.InputPurpose.URL`) and Done
otherwise. `TvStage` follows the keyboard by itself: its `docked` property is
"keyboard" while it is up, so `TvToast` and an action bar rise above it
(`TV["keyboard"]["above"]`).

The shell side is `org.projectluma.Shell.Keyboard1` on the session bus
(`SetActionLabel(s)`, properties `Visible` and `Height`); see
docs/developer-platform/tv-mode.md ("Typing").
"""
from __future__ import annotations

import weakref
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

__all__ = ["set_keyboard_action", "keyboard_action", "watch_tv_keyboard", "tv_keyboard_state", "dock_stage"]

BUS_NAME = "org.projectluma.Shell.Keyboard"
OBJECT_PATH = "/org/projectluma/Shell/Keyboard"
INTERFACE = "org.projectluma.Shell.Keyboard1"
_ATTR = "_lumaui_keyboard_action"


class _Shell:
    """The shell keyboard's state, shared by the process; quiet without the shell."""

    def __init__(self) -> None:
        self.visible = False
        self.height = 0.0
        self._watchers: list[Callable[[bool, float], None]] = []
        self._proxy: Gio.DBusProxy | None = None
        self._sent: str | None = None
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            BUS_NAME, OBJECT_PATH, INTERFACE, None, self._ready)

    def _ready(self, _source, result) -> None:
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            return
        self._proxy.connect("g-properties-changed", lambda *_a: self._read())
        self._proxy.connect("notify::g-name-owner", lambda *_a: self._read())
        self._read()

    def _read(self) -> None:
        proxy = self._proxy
        visible = height = None
        if proxy is not None and proxy.get_name_owner():
            visible = proxy.get_cached_property("Visible")
            height = proxy.get_cached_property("Height")
        visible = bool(visible.unpack()) if visible is not None else False
        height = float(height.unpack()) if height is not None and visible else 0.0
        if (visible, height) == (self.visible, self.height):
            return
        self.visible, self.height = visible, height
        for watcher in list(self._watchers):
            watcher(visible, height)

    def watch(self, callback: Callable[[bool, float], None]) -> Callable[[], None]:
        self._watchers.append(callback)
        callback(self.visible, self.height)

        def stop() -> None:
            if callback in self._watchers:
                self._watchers.remove(callback)
        return stop

    def send(self, label: str, *, again: bool = False) -> None:
        if self._proxy is None or (label == self._sent and not again):
            return
        self._sent = label
        self._proxy.call("SetActionLabel", GLib.Variant("(s)", (label,)),
                         Gio.DBusCallFlags.NO_AUTO_START, -1, None, None)


_shell: _Shell | None = None


def _get() -> _Shell:
    global _shell
    if _shell is None:
        _shell = _Shell()
    return _shell


def keyboard_action(widget: Gtk.Widget | None) -> str:
    """The action label set for `widget` or the nearest ancestor that has one."""
    while widget is not None:
        label = getattr(widget, _ATTR, None)
        if label is not None:
            return label
        widget = widget.get_parent()
    return ""


def set_keyboard_action(widget: Gtk.Widget, label: str | None) -> None:
    """Label the TV keyboard's action key while `widget` (a text field) has the focus.

    One short verb for what Enter does here ("Search", "Add", "Go"); None or
    "" returns to the field's own (Go for an address, else Done). Outside TV
    posture this does nothing visible.
    """
    first = getattr(widget, _ATTR, None) is None
    setattr(widget, _ATTR, label or "")
    if first:
        focus = Gtk.EventControllerFocus()
        ref = weakref.ref(widget)
        focus.connect("enter", lambda *_a: _focus(ref(), True))
        focus.connect("leave", lambda *_a: _focus(ref(), False))
        widget.add_controller(focus)
        widget.connect("realize", lambda w: _follow_window(w))
        if widget.get_realized():
            _follow_window(widget)
    if widget.has_focus() or (widget.get_focus_child() is not None):
        _focus(widget, True)


def _follow_window(widget: Gtk.Widget) -> None:
    # The shell keeps the label for the window that has the focus, so a field
    # focused before its window was (a window opening) says it again then.
    root = widget.get_root()
    if not isinstance(root, Gtk.Window) or getattr(root, "_lumaui_keyboard_follow", False):
        return
    root._lumaui_keyboard_follow = True
    ref = weakref.ref(root)

    def active(*_a) -> None:
        window = ref()
        if window is None or not window.is_active():
            return
        focused = window.get_focus()
        label = keyboard_action(focused) if focused is not None else ""
        if label:
            _focus(focused, True, again=True)

    root.connect("notify::is-active", active)


def _focus(widget: Gtk.Widget | None, entered: bool, *, again: bool = False) -> None:
    if widget is None:
        return
    from .lumaui import tv_posture
    if not tv_posture():
        return
    _get().send(keyboard_action(widget) if entered else "", again=again)


def tv_keyboard_state() -> tuple[bool, float]:
    """(visible, height in TV units) as last reported by the shell."""
    shell = _get()
    return shell.visible, shell.height


def watch_tv_keyboard(callback: Callable[[bool, float], None]) -> Callable[[], None]:
    """Call `callback(visible, height)` now and whenever the TV keyboard shows, hides or resizes.

    `height` is in TV units (one pixel of a 1920x1080 layout; 428 for the
    standard keyboard), 0 while hidden. Returns the function that stops
    watching. Outside TV posture the keyboard never reports itself visible.
    """
    return _get().watch(callback)


def dock_stage(stage) -> Callable[[], None]:
    """Keep `stage.docked` at "keyboard" while the TV keyboard is up (TvStage does this itself)."""
    ref = weakref.ref(stage)
    state = {"before": None}

    def changed(visible: bool, _height: float) -> None:
        target = ref()
        if target is None:
            return
        if visible and target.docked != "keyboard":
            state["before"] = target.docked
            target.docked = "keyboard"
        elif not visible and target.docked == "keyboard":
            target.docked = state["before"] or ""
            state["before"] = None

    return watch_tv_keyboard(changed)
