# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: the tooltip for a small target.

A device icon in a "Runs on" row, a Share or bin button: hovering names it in a
small light label just above, centred on the target (Depot `.dq-ri::after`:
4 by 9 padding, 8 round, 600 11.5 px, a soft drop). It is its own surface, so
it never inherits a dimmed parent's opacity. The target also gets the name as
its accessible label, so a screen reader and a keyboard user hear the same.

    Tooltip.attach(icon_box, "TV")
    Tooltip.attach(share_button, "Share")
    Tooltip.attach(update_button, "1.0.158 → 1.0.159 · 96 MB")   # a key's detail

Focus shows it too, so the name is reachable without a pointer.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import lumaui  # noqa: E402

__all__ = ["Tooltip"]


class Tooltip(Gtk.Popover):
    """The label's surface. Use `Tooltip.attach(widget, text)`."""

    __gtype_name__ = "LumaUITooltip"

    def __init__(self, text: str) -> None:
        super().__init__(autohide=False, has_arrow=False, position=Gtk.PositionType.TOP,
                         can_focus=False, can_target=False)
        self.add_css_class("lumaui-tooltip")
        self.label = Gtk.Label(label=text)
        self.set_child(self.label)
        self.set_offset(0, -6)
        self._timer = 0

    @classmethod
    def attach(cls, widget: Gtk.Widget, text: str, *, label: bool = True) -> "Tooltip":
        """Show `text` over `widget` on hover or focus; `label=False` keeps the widget's own accessible name."""
        old = getattr(widget, "_lumaui_tooltip", None)
        if old is not None:
            old.set_text(text)
            return old
        tip = cls(text)
        tip.set_parent(widget)
        widget._lumaui_tooltip = tip
        widget.set_has_tooltip(False)
        if label:
            widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: tip.later())
        motion.connect("leave", lambda *_a: tip.hide_now())
        widget.add_controller(motion)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_a: tip.later())
        focus.connect("leave", lambda *_a: tip.hide_now())
        widget.add_controller(focus)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_a: tip.hide_now())
        widget.add_controller(click)
        widget.connect("destroy", lambda *_a: tip.hide_now())
        widget.connect("unrealize", lambda *_a: tip.hide_now())
        return tip

    @property
    def text(self) -> str:
        return self.label.get_label()

    def set_text(self, text: str) -> None:
        self.label.set_label(text)

    def later(self) -> None:
        self._cancel()
        delay = lumaui.duration("tooltip_delay")
        self._timer = GLib.timeout_add(delay, self._show)

    def _show(self) -> bool:
        self._timer = 0
        parent = self.get_parent()
        if parent is not None and parent.get_mapped():
            self.popup()
        return False

    def _cancel(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def hide_now(self) -> None:
        self._cancel()
        if self.get_visible():
            self.popdown()
