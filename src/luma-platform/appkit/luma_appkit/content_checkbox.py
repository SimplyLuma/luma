# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: the checkbox.

16 square, 5 round, sunk in a well; on, it fills with the accent and shows a
white check (Depot `.dq-chk`, the Filters menu and the grown Install on…
panel). It is the mark only: the row around it (a menu row, a device row) is
the control, so the box never takes a click of its own unless it stands alone.

    box = Checkbox(checked=True)
    box.set_checked(False)
    Checkbox(checked=False, label="Keep matched", on_toggle=keep)   # standalone: clickable, named
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from . import icons, lumaui  # noqa: E402

__all__ = ["Checkbox"]


class Checkbox(Gtk.Box):
    """The box. Standalone (with `on_toggle`) it is focusable and toggles on click, Space or Enter."""

    __gtype_name__ = "LumaUICheckbox"

    def __init__(self, *, checked: bool = False, label: str | None = None,
                 on_toggle: Callable[[bool], None] | None = None, sensitive: bool = True) -> None:
        standalone = on_toggle is not None
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, sensitive=sensitive,
                         accessible_role=Gtk.AccessibleRole.CHECKBOX if standalone
                         else Gtk.AccessibleRole.PRESENTATION)
        self.add_css_class("lumaui-checkbox")
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.mark = icons.image("check")
        self.mark.set_hexpand(True)  # centred in the box; the box itself never expands (set above)
        self.mark.set_halign(Gtk.Align.CENTER)
        self.append(self.mark)
        self.on_toggle = on_toggle
        self._checked = False
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if standalone:
            self.set_focusable(True)
            self.set_can_target(True)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a: self.toggle())
            self.add_controller(click)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._key)
            self.add_controller(keys)
        else:
            self.set_can_target(False)
        self.set_checked(checked)

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        from gi.repository import Gdk
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.toggle()
            return True
        return False

    @property
    def checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool) -> None:
        self._checked = bool(checked)
        lumaui.set_css_class(self, "on", self._checked)
        self.update_state([Gtk.AccessibleState.CHECKED],
                          [GObject.Value(GObject.TYPE_INT, int(Gtk.AccessibleTristate.TRUE if self._checked
                                                               else Gtk.AccessibleTristate.FALSE))])

    def toggle(self) -> None:
        if not self.get_sensitive():
            return
        self.set_checked(not self._checked)
        if self.on_toggle is not None:
            self.on_toggle(self._checked)
