# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the edge-fade scroller, a sideways strip that fades where there is more.

A horizontal strip (reviews, thumbnails) fades out at an edge only while
content continues past it (Depot 3.10, `.dq-reviews`/`.dq-shots`): the fade is
an alpha mask over `fade` px that eases in and out over 0.25 s as the strip
scrolls. It re-measures whenever the strip's extent changes, so an image that
finishes loading after the first layout still gets its fade.

`arrows=True` adds round key-coloured buttons over the fades (34 px, 6 px past
each edge): the left one appears once scrolled, the right one goes at the end.
A phone swipes instead, so `set_arrows(False)` there.

    strip = EdgeFadeScroller(cards_box, fade=56, arrows=True)
    thumbs = EdgeFadeScroller(thumb_row, fade=72)
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import icons, lumaui  # noqa: E402

__all__ = ["EdgeFadeScroller"]


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


class EdgeFadeScroller(Gtk.Widget):
    """The strip: a horizontal scroller, its two fades and, optionally, two arrows."""

    __gtype_name__ = "LumaUIEdgeFadeScroller"

    def __init__(self, child: Gtk.Widget, *, fade: int = 56, arrows: bool = False) -> None:
        super().__init__()
        self.add_css_class("lumaui-edge-fade")
        self.fade = fade
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_height=True, hexpand=True)
        self.scroller.set_child(child)
        self.scroller.set_parent(self)
        self.child = child
        self.prev = self._arrow("chevron-left", "Previous", -1)
        self.next = self._arrow("chevron-right", "Next", 1)
        self._arrows = False
        # 1 means "at that end": no fade there (the mockup's --fl/--fr), eased toward the target.
        self._left, self._right = 1.0, 1.0
        self._target = (1.0, 1.0)
        self._from = (1.0, 1.0)
        self._started = 0
        self._tick = 0
        adjustment = self.scroller.get_hadjustment()
        adjustment.connect("changed", lambda _a: self._measure())
        adjustment.connect("value-changed", lambda _a: self._measure())
        self.set_arrows(arrows)

    def _arrow(self, icon: str, label: str, step: int) -> Gtk.Button:
        button = Gtk.Button(tooltip_text=label, valign=Gtk.Align.CENTER)
        button.add_css_class("lumaui-edge-fade-arrow")
        button.set_child(icons.image(icon))
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: self.page(step))
        button.set_parent(self)
        return button

    # ── the person's controls ─────────────────────────────────────────────

    def set_arrows(self, arrows: bool) -> None:
        self._arrows = arrows
        self._measure()

    def page(self, step: int) -> None:
        """Scroll most of a view's width in `step`'s direction."""
        adjustment = self.scroller.get_hadjustment()
        target = adjustment.get_value() + step * adjustment.get_page_size() * 0.8
        target = max(adjustment.get_lower(), min(target, adjustment.get_upper() - adjustment.get_page_size()))
        if lumaui.reduced_motion():
            adjustment.set_value(target)
            return
        start, began = adjustment.get_value(), GLib.get_monotonic_time()

        def step_scroll(_widget, _clock) -> bool:
            t = min(1.0, (GLib.get_monotonic_time() - began) / 1000 / lumaui.duration("edge_fade"))
            adjustment.set_value(start + (target - start) * _ease(t))
            return t < 1.0
        self.add_tick_callback(step_scroll)

    # ── measuring ─────────────────────────────────────────────────────────

    def ends(self) -> tuple[bool, bool]:
        """Whether the strip is at its left and right ends (no fade there)."""
        adjustment = self.scroller.get_hadjustment()
        maximum = adjustment.get_upper() - adjustment.get_page_size()
        value = adjustment.get_value()
        return value <= adjustment.get_lower() + 2, value >= maximum - 2

    def _measure(self) -> None:
        at_left, at_right = self.ends()
        target = (1.0 if at_left else 0.0, 1.0 if at_right else 0.0)
        for button, hidden in ((self.prev, at_left), (self.next, at_right)):
            shown = self._arrows and not hidden
            button.set_visible(self._arrows)
            button.set_opacity(1.0 if shown else 0.0)
            button.set_can_target(shown)
            button.set_can_focus(shown)
        if target == self._target:
            return
        self._from, self._target = (self._left, self._right), target
        if lumaui.reduced_motion() or not self.get_mapped():
            self._left, self._right = target
            self.queue_draw()
            return
        self._started = GLib.get_monotonic_time()
        if not self._tick:
            self._tick = self.add_tick_callback(self._animate)

    def _animate(self, _widget, _clock) -> bool:
        t = min(1.0, (GLib.get_monotonic_time() - self._started) / 1000 / max(1, lumaui.duration("edge_fade")))
        k = _ease(t)
        self._left = self._from[0] + (self._target[0] - self._from[0]) * k
        self._right = self._from[1] + (self._target[1] - self._from[1]) * k
        self.queue_draw()
        if t >= 1.0:
            self._tick = 0
            return False
        return True

    @property
    def fades(self) -> tuple[float, float]:
        """The mask's alpha at the left and right edges (1 = no fade)."""
        return self._left, self._right

    # ── layout and drawing ────────────────────────────────────────────────

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        minimum, natural, _b, _n = self.scroller.measure(orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum = 0
        return minimum, natural, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        self.scroller.allocate(width, height, -1, None)
        for button, x in ((self.prev, None), (self.next, None)):
            size = max(button.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 1)
            outside = 6
            left = -outside if button is self.prev else width - size + outside
            transform = Gsk.Transform().translate(Graphene.Point().init(left, (height - size) / 2))
            button.allocate(size, size, -1, transform)
        GLib.idle_add(lambda: (self._measure(), False)[1])

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        if self._left >= 0.999 and self._right >= 0.999:
            self.snapshot_child(self.scroller, snapshot)
        else:
            fade = min(self.fade, width / 2) / width
            stops = []
            for offset, alpha in ((0.0, self._left), (fade, 1.0), (1.0 - fade, 1.0), (1.0, self._right)):
                colour = Gdk.RGBA()
                colour.red = colour.green = colour.blue = 0.0
                colour.alpha = alpha
                stop = Gsk.ColorStop()
                stop.offset, stop.color = offset, colour
                stops.append(stop)
            bounds = Graphene.Rect().init(0, 0, width, height)
            snapshot.push_mask(Gsk.MaskMode.ALPHA)
            snapshot.append_linear_gradient(bounds, Graphene.Point().init(0, 0), Graphene.Point().init(width, 0), stops)
            snapshot.pop()
            self.snapshot_child(self.scroller, snapshot)
            snapshot.pop()
        if self._arrows:
            self.snapshot_child(self.prev, snapshot)
            self.snapshot_child(self.next, snapshot)

    def do_dispose(self) -> None:
        for child in (self.scroller, self.prev, self.next):
            if child is not None and child.get_parent() is self:
                child.unparent()
        Gtk.Widget.do_dispose(self)
