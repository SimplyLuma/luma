# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: how parts learn the width of the place they appear in.

Width adapts layout, never identity. A details pane becomes a drawer, a
sidebar opens from the left, a mode switch keeps only its current label,
when the *window* (or the part of it that stands for one, such as the
Gallery's phone frame) crosses a kit breakpoint. GTK has no signal for that:
a widget whose own size did not change is not reallocated when its window
narrows. `watch_width` is the kit's primitive for it. It listens to the
root window even when the part is hidden, measures the nearest
`LayerHost` named "window" (or the window itself) after the frame allocation, and calls
back only when the answer changes.

Parts adapt on their own. An app that decides its phone bars when it draws
(v71 "redraw on crossing the phone width") watches the tier instead:

    watch = WidthWatch(window, on_tier=lambda tier: self.redraw())   # "phone" | "compact" | "regular"
    watch.connect("tier-changed", lambda _w, tier: ...)              # the same, as a GObject signal
    tier(widget)                                                     # the tier now

The tiers (v71, 2026-09-29 mobile layer): phone under 560, compact 560 to 900,
regular over 900. A window crossing 560 redraws in the other shape.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from . import icons  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["WidthWatch", "TIERS", "tier", "tier_for", "window_tier"]

#: v71 tiers, narrowest first. Phone is under PHONE_BELOW (560), regular over REGULAR_ABOVE (900).
TIERS = ("phone", "compact", "regular")
PHONE_BELOW = tokens.PHONE_MAX_WIDTH + 1
REGULAR_ABOVE = 900


def tier_for(width: int) -> str:
    """The tier of a window `width` wide: "phone" (<560), "compact" (560-900) or "regular" (>900)."""
    if width < PHONE_BELOW:
        return "phone"
    return "compact" if width <= REGULAR_ABOVE else "regular"


def window_width(widget: Gtk.Widget) -> int:
    """The width of the window `widget` is in, or of the frame standing for one."""
    from .structure_layers import LayerHost

    node: Gtk.Widget | None = widget
    while node is not None:
        if isinstance(node, LayerHost) and node.layer_name == "window":
            return node.get_width()
        node = node.get_parent()
    root = widget.get_root()
    return root.get_width() if root is not None else 0


def is_phone(widget: Gtk.Widget) -> bool:
    """Whether the window `widget` is in is phone-width (v71: under 560)."""
    width = window_width(widget)
    return 0 < width <= tokens.PHONE_MAX_WIDTH


def tier(widget: Gtk.Widget) -> str:
    """The tier of the window `widget` is in; "regular" before it has a width."""
    width = window_width(widget)
    return tier_for(width) if width > 0 else "regular"


class WidthWatch(GObject.Object):
    """Calls `callback(width)` after layout whenever the window's width changes.

    `threshold`, when given, reduces the callback to crossings: it is called
    with the width only when `width <= threshold` changes truth.

    The tier: `watch.tier` is the window's tier ("phone", "compact", "regular"),
    `on_tier(tier)` and the `tier-changed` signal fire when it changes (and once
    on the first measurement), so an app redraws its phone or desktop shape.
    """

    __gtype_name__ = "LumaUIWidthWatch"
    __gsignals__ = {
        "tier-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "width-changed": (GObject.SignalFlags.RUN_LAST, None, (int,)),
    }

    def __init__(self, widget: Gtk.Widget, callback: Callable[[int], None] | None = None, *,
                 threshold: int | None = None, on_tier: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self.widget, self.callback, self.threshold = widget, callback, threshold
        self.on_tier = on_tier
        self._tier: str | None = None
        self._last: object = None
        self._root = None
        self._root_handlers = []
        self._surface = None
        self._surface_handler = 0
        self._pending = 0
        self._frame_clock = None
        self._frame_handler = 0
        self._waiting = False
        widget.connect("realize", self._realized)
        widget.connect("unrealize", self._unrealized)
        widget.connect("map", lambda *_a: self.schedule())
        widget.connect("notify::root", self._rooted)
        self._rooted(widget)

    def _rooted(self, _widget: Gtk.Widget, *_args) -> None:
        # A phone can hide its sidebar toggle before it ever realizes. The
        # window still owns the layout that toggle must adapt.
        root = self.widget.get_root()
        if root is self._root:
            return
        self._unrealized(None)
        if self._root is not None:
            for handler in self._root_handlers:
                self._root.disconnect(handler)
        self._root = root
        self._root_handlers = []
        if root is not None:
            self._root_handlers = [
                root.connect("realize", self._realized),
                root.connect("unrealize", self._unrealized),
                root.connect("map", lambda *_a: self.schedule()),
            ]
            if root.get_realized():
                self._realized(root)

    def _realized(self, _widget: Gtk.Widget) -> None:
        native = self.widget.get_native()
        surface = native.get_surface() if native is not None else None
        if surface is None or surface is self._surface:
            return
        self._unrealized(None)
        self._surface = surface
        self._surface_handler = surface.connect("notify::width", lambda *_a: self.schedule())
        self.schedule()

    def _unrealized(self, _widget: Gtk.Widget | None) -> None:
        if self._surface is not None and self._surface_handler:
            self._surface.disconnect(self._surface_handler)
        self._surface, self._surface_handler = None, 0
        self._cancel_frame()
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0

    def _cancel_frame(self) -> None:
        if self._frame_clock is not None and self._frame_handler:
            self._frame_clock.disconnect(self._frame_handler)
        self._frame_clock, self._frame_handler = None, 0

    def _after_paint(self, _clock) -> None:
        self._cancel_frame()
        self._check()

    def schedule(self) -> None:
        """Measure after allocation, including compositor-driven resizes."""
        root = self.widget.get_root()
        clock = root.get_frame_clock() if root is not None else None
        if clock is not None:
            if self._pending:
                GLib.source_remove(self._pending)
                self._pending = 0
            if not self._frame_handler:
                self._frame_clock = clock
                self._frame_handler = clock.connect("after-paint", self._after_paint)
            clock.request_phase(Gdk.FrameClockPhase.AFTER_PAINT)
        elif not self._pending:
            self._pending = GLib.idle_add(self._check)

    def check(self) -> None:
        """Measure now (tests, and parts that have just been shown)."""
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0
        self._check()

    def _check(self) -> bool:
        self._pending = 0
        width = window_width(self.widget)
        if width <= 0:
            # Shown before the window's first layout: measure on the next frame.
            frame_widget = self.widget.get_root() or self.widget
            if frame_widget.get_mapped() and not self._waiting:
                self._waiting = True

                def tick(_widget: Gtk.Widget, _clock: object) -> bool:
                    self._waiting = False
                    self.schedule()
                    return GLib.SOURCE_REMOVE

                frame_widget.add_tick_callback(tick)
            return False
        value: object = width if self.threshold is None else width <= self.threshold
        if value != self._last:
            self._last = value
            if self.callback is not None:
                self.callback(width)
            self.emit("width-changed", width)
        now = tier_for(width)
        if now != self._tier:
            self._tier = now
            if self.on_tier is not None:
                self.on_tier(now)
            self.emit("tier-changed", now)
        return False

    @property
    def tier(self) -> str:
        """The tier at the last measurement (measures now if there was none)."""
        if self._tier is None:
            return tier(self.widget)
        return self._tier

    @property
    def phone(self) -> bool:
        return self.tier == "phone"


def window_tier(window: Gtk.Widget) -> WidthWatch:
    """The window's own tier watch (one per window), which also keeps its tier class.

    The window carries `lumaui-phone`, `lumaui-compact` or `lumaui-regular`, so kit
    sheets can say what a phone hides (keyboard hints, bar-field focus rings).
    """
    watch = getattr(window, "_lumaui_tier_watch", None)
    if watch is None:
        def mark(now: str) -> None:
            for name in TIERS:
                (window.add_css_class if name == now else window.remove_css_class)(f"lumaui-{name}")
        watch = WidthWatch(window, on_tier=mark)
        window._lumaui_tier_watch = watch
    return watch


def icon_button(icon: str, label: str, css: str, *, toggle: bool = False,
                shortcut: str | None = None) -> Gtk.Button:
    """A quiet icon button: the glyph, its name as tooltip and accessible label."""
    button: Gtk.Button = Gtk.ToggleButton() if toggle else Gtk.Button()
    button.add_css_class(css)
    button.set_child(icons.image(icon))
    button.remove_css_class("image-button")  # a LumaUI part, not the legacy icon-button look
    button.set_valign(Gtk.Align.CENTER)
    button.set_tooltip_text(f"{label} ({shortcut})" if shortcut else label)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    if shortcut:
        button.update_property([Gtk.AccessibleProperty.KEY_SHORTCUTS], [shortcut])
    return button


def arrow_keys(box: Gtk.Widget, *, orientation: Gtk.Orientation = Gtk.Orientation.HORIZONTAL,
               activate: bool = False) -> None:
    """Arrows move focus between the focusable children of `box` (a group), wrapping.

    With `activate`, moving also activates the control reached (a radio group).
    """
    keys = Gtk.EventControllerKey()
    from gi.repository import Gdk

    forward = (Gdk.KEY_Right,) if orientation == Gtk.Orientation.HORIZONTAL else (Gdk.KEY_Down,)
    backward = (Gdk.KEY_Left,) if orientation == Gtk.Orientation.HORIZONTAL else (Gdk.KEY_Up,)

    def pressed(_c, keyval: int, _code: int, _state) -> bool:
        if keyval not in forward + backward:
            return False
        items = [c for c in _children(box) if c.get_visible() and c.is_sensitive() and c.get_focusable()]
        if not items:
            return False
        root = box.get_root()
        focus = root.get_focus() if root is not None else None
        current = next((i for i, w in enumerate(items) if focus is not None and (focus is w or focus.is_ancestor(w))), -1)
        step = 1 if keyval in forward else -1
        target = items[(current + step) % len(items)]
        target.grab_focus()
        if activate:
            target.activate()
        return True

    keys.connect("key-pressed", pressed)
    box.add_controller(keys)


def _children(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def children(widget: Gtk.Widget) -> list[Gtk.Widget]:
    return list(_children(widget))
