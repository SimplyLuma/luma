# SPDX-License-Identifier: Apache-2.0
"""LumaUI bar: the bar's frame on a phone (v71 second pass).

On a phone one place holds every choice: the bar's place at the foot. A menu
(v71 `lMenu`, an app's own pop), the system confirm (`lConfirm`) and a sheet
(an app asking for more, `.mdsheet`) all rise from there, in the bar's frame:
on the 16 px gutter, 34 px up, 26 px corners, with no grabber.

- **menu**: over a light scrim that closes it; up to the window less 140,
  scrolling; rows of 48 at 16 (`PanelRow`).
- **confirm**: title and line left-aligned with no icon, Cancel and the red
  action side by side at 48; the scrim nudges rather than closes.
- **sheet**: up to the window less 120, scrolling, above everything in the
  window; the scrim closes it.

    BarFrame.present(button, panel_list(rows), kind="menu")
    BarFrame.present(button, form, kind="sheet", title="Add a provider")

`menus.bar_menu`, `FloatingMenu`, `DestructiveDialog` and
`ActionCenter.sheet` present through it at phone width; apps rarely call it
directly. CSS: luma-appkit-bar.css, `/* LumaUI: Bar frame (v71) */`.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .structure_layers import LayerHost, ModalHandle  # noqa: E402

__all__ = ["BarFrame", "FRAME_KINDS"]

FRAME_KINDS = ("menu", "confirm", "sheet")


class BarFrame(Gtk.Box):
    """The card in the bar's frame. Show one with `BarFrame.present(...)`."""

    __gtype_name__ = "LumaUIBarFrame"

    def __init__(self, content: Gtk.Widget, *, kind: str = "menu", title: str | None = None) -> None:
        if kind not in FRAME_KINDS:
            raise ValueError(f"a bar frame holds a {', '.join(FRAME_KINDS)}")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL, valign=Gtk.Align.END)
        self.add_css_class("lumaui-bar-frame")
        self.add_css_class(kind)
        self.kind, self.content = kind, content
        self.handle: ModalHandle | None = None
        self.on_closed: Callable[[], None] | None = None
        metrics = tokens.ACTION_CENTER
        self.set_margin_start(metrics["frame_side"])
        self.set_margin_end(metrics["frame_side"])
        self.set_margin_bottom(metrics["frame_bottom"])
        if title:
            heading = Gtk.Label(label=title, xalign=0)
            heading.add_css_class("lumaui-bar-frame-title")
            self.append(heading)
            self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        self.scroller.add_css_class("lumaui-bar-frame-body")
        self.scroller.set_child(content)
        self.append(self.scroller)

    @staticmethod
    def configure(where: Gtk.Widget, *, bottom: int | None = None, max_fraction: float | None = None,
                  opaque: bool | None = None) -> None:
        """For an app whose own controls hold the foot (Camera's shutter): frames in this window rise
        from `bottom` instead of 34, are at most `max_fraction` of the window tall, and are opaque."""
        host = LayerHost.window_host(where)
        options = dict(getattr(host, "_bar_frame_options", {}))
        for key, value in (("bottom", bottom), ("max_fraction", max_fraction), ("opaque", opaque)):
            if value is not None:
                options[key] = value
        host._bar_frame_options = options

    @classmethod
    def present(cls, where: Gtk.Widget, content: Gtk.Widget, *, kind: str = "menu", title: str | None = None,
                on_cancel: Callable[[], None] | None = None, initial_focus: Gtk.Widget | None = None,
                centred: bool = False, bottom: int | None = None, max_fraction: float | None = None,
                opaque: bool | None = None, anchor_width: int | None = None) -> "BarFrame":
        """Rise from the bar's place in the window `where` is in (`centred`: a card in the middle, as a
        sheet in a window). `bottom`, `max_fraction` and `opaque` override `configure()`.
        `anchor_width` presents an undimmed fixed-width menu over the source key."""
        if anchor_width is not None and (isinstance(anchor_width, bool) or not isinstance(anchor_width, int) or anchor_width <= 0):
            raise ValueError("anchor_width must be a positive integer")
        frame = cls(content, kind=kind, title=title)
        host = LayerHost.window_host(where)
        options = dict(getattr(host, "_bar_frame_options", {}))
        for key, value in (("bottom", bottom), ("max_fraction", max_fraction), ("opaque", opaque)):
            if value is not None:
                options[key] = value
        frame._options = options
        if options.get("bottom") is not None:
            frame.set_margin_bottom(int(options["bottom"]))
        opaque_now = bool(options.get("opaque")) or lumaui.in_media_context(where)
        lumaui.set_css_class(frame, "opaque", opaque_now)
        lumaui.set_css_class(frame, "lumaui-media", opaque_now)  # media inks on the dark surface
        frame.cap(host.get_height())
        frame.handle = host.present_modal(frame, on_cancel=frame._cancelled, initial_focus=initial_focus,
                                          drawer=False, scrim=anchor_width is None)
        frame._on_cancel = on_cancel
        if anchor_width is not None:
            frame._anchor_widget, frame._anchor_host, frame._anchor_width = where, host, anchor_width
            frame.add_css_class("anchored")
            frame.set_halign(Gtk.Align.START)
            frame.set_valign(Gtk.Align.END)
            frame.set_margin_end(0)
            frame._anchor_clock = None
            frame.connect("map", frame._watch_anchor)
            frame.connect("unmap", frame._unwatch_anchor)
            frame._place_anchor()
            if frame.get_mapped():
                frame._watch_anchor(frame)
        elif centred:
            frame.add_css_class("centred")
            for margin in (frame.set_margin_start, frame.set_margin_end, frame.set_margin_bottom):
                margin(0)
            frame.set_size_request(min(480, max(320, host.get_width() - 48)) if host.get_width() > 0 else 480, -1)
        else:
            # present_modal centres a card; the frame sits at the foot on the gutter.
            frame.set_halign(Gtk.Align.FILL)
            frame.set_valign(Gtk.Align.END)
        frame.handle.scrim.add_css_class("bar-frame")
        frame.handle.drawer = kind != "confirm"  # a tap beside a menu or sheet closes it; a confirm nudges
        if host.get_height() <= 0:
            GLib.idle_add(lambda: (frame.cap(host.get_height()), False)[1])
        return frame

    def _watch_anchor(self, _widget) -> None:
        if self._anchor_clock is None:
            self._anchor_clock = self.get_frame_clock()
            self._anchor_handler = self._anchor_clock.connect_after("after-paint", self._place_anchor)

    def _unwatch_anchor(self, _widget) -> None:
        if self._anchor_clock is not None:
            self._anchor_clock.disconnect(self._anchor_handler)
            self._anchor_clock = None

    def _place_anchor(self, *_args) -> None:
        host, anchor = self._anchor_host, self._anchor_widget
        ok, bounds = anchor.compute_bounds(host)
        if not ok or anchor.get_allocated_width() <= 0 or host.get_height() <= 0:
            return
        side = tokens.ACTION_CENTER["frame_side"]
        width = min(self._anchor_width, max(1, host.get_width() - 2 * side))
        left = round(max(side, min(bounds.get_x(), host.get_width() - side - width)))
        bottom = round(max(side, host.get_height() - bounds.get_y() - bounds.get_height()))
        if self.get_margin_start() != left:
            self.set_margin_start(left)
        if self.get_margin_bottom() != bottom:
            self.set_margin_bottom(bottom)
        if self.get_size_request()[0] != width:
            self.set_size_request(width, -1)

    def cap(self, height: int) -> None:
        """Up to the window less 140 (a menu or confirm) or 120 (a sheet), then the content scrolls."""
        if height <= 0:
            return
        metrics = tokens.ACTION_CENTER
        options = getattr(self, "_options", {})
        if options.get("max_fraction"):
            self.scroller.set_max_content_height(max(96, int(height * options["max_fraction"])))
            return
        room = metrics["frame_sheet_room"] if self.kind == "sheet" else metrics["frame_menu_room"]
        self.scroller.set_max_content_height(max(96, height - room - metrics["frame_bottom"]))

    @property
    def is_open(self) -> bool:
        return self.handle is not None and not self.handle.closed

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self._closed()

    def fold_panel(self) -> None:
        """A panel row acted: the frame closes (the same as a grown bar folding)."""
        self.close()

    def _cancelled(self) -> None:
        self._closed()
        callback = getattr(self, "_on_cancel", None)
        if callback is not None:
            callback()

    def _closed(self) -> None:
        callback, self.on_closed = self.on_closed, None
        if callback is not None:
            callback()


def phone(where: Gtk.Widget) -> bool:
    """Whether choices asked from `where` rise from the bar's frame (a phone, or a phone-width window)."""
    try:
        host = LayerHost.window_host(where)
    except ValueError:
        return lumaui.mobile_form_factor()
    return lumaui.mobile_form_factor() or lumaui.is_phone_width(host)
