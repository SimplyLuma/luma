# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the remote's focus.

One visible focus at a time, moved spatially: an arrow goes to the nearest
focusable in that direction (`nearest()`). An app registers what can take the
focus and what happens there; LumaUI owns the choice and the look:

    focus = TvFocus(stage, on_back=lambda: False)
    for place in places:
        focus.add(tab, group="places", on_focus=open_place)       # moving opens it, no OK
    focus.add(band, group="band", on_key=scrub)                   # Left/Right scrub until the ends
    for card in days:
        focus.add(card, group="days", on_activate=pick)
    focus.set_group_entry("days", lambda: days[selected])        # Down lands on the chosen day
    focus.focus(band)

The focused widget carries the `tv-focus` style class (and `TvLift` children
lift), and holds GTK focus, so accessibility follows the remote and leaving a
text field takes GTK focus (and the system TV keyboard) with it. A scope (`push_scope`) confines the focus to a sheet until it closes,
then returns it where it came from. A grab (`grab`, the TV keyboard) takes
every key while it is up. Filters (`add_filter`, the idle hook) see each key
first. Back that nothing takes falls through to the window, which returns to
the TV home at the app's top level.

Keys: arrows, OK (Return, KP_Enter, Select), Back (Escape, Back, BackSpace
outside text). No pointer is needed and nothing depends on hover.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["TvFocus", "nearest", "LEFT", "RIGHT", "UP", "DOWN", "direction_for"]

LEFT, RIGHT, UP, DOWN = "left", "right", "up", "down"
_ARROWS = {Gdk.KEY_Left: LEFT, Gdk.KEY_Right: RIGHT, Gdk.KEY_Up: UP, Gdk.KEY_Down: DOWN,
           Gdk.KEY_KP_Left: LEFT, Gdk.KEY_KP_Right: RIGHT, Gdk.KEY_KP_Up: UP, Gdk.KEY_KP_Down: DOWN}
OK_KEYS = frozenset({Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_Select, Gdk.KEY_space})
BACK_KEYS = frozenset({Gdk.KEY_Escape, Gdk.KEY_Back, Gdk.KEY_BackSpace})

Rect = tuple[float, float, float, float]   # x, y, width, height


def direction_for(keyval: int) -> str | None:
    return _ARROWS.get(keyval)


def nearest(origin: Rect, candidates: Iterable[tuple[object, Rect]], direction: str,
            *, cross_weight: float | None = None) -> object | None:
    """The candidate nearest `origin` in `direction`, or None.

    A candidate counts when its centre lies beyond the origin's centre in that
    direction. Score: the gap along the direction (edge to edge, never below
    zero) plus `cross_weight` times the gap across it (zero while the two
    overlap across), so the item straight ahead wins over a closer diagonal;
    ties go to the candidate whose centre is nearest across.
    """
    weight = tokens.TV["focus"]["cross_weight"] if cross_weight is None else cross_weight
    ox, oy, ow, oh = origin
    ocx, ocy = ox + ow / 2, oy + oh / 2
    best, best_score = None, None
    for key, (x, y, w, h) in candidates:
        cx, cy = x + w / 2, y + h / 2
        if direction == RIGHT:
            ok, along, across = cx > ocx + 0.5, x - (ox + ow), _gap(y, h, oy, oh)
        elif direction == LEFT:
            ok, along, across = cx < ocx - 0.5, ox - (x + w), _gap(y, h, oy, oh)
        elif direction == DOWN:
            ok, along, across = cy > ocy + 0.5, y - (oy + oh), _gap(x, w, ox, ow)
        else:
            ok, along, across = cy < ocy - 0.5, oy - (y + h), _gap(x, w, ox, ow)
        if not ok:
            continue
        offset = abs(cy - ocy) if direction in (LEFT, RIGHT) else abs(cx - ocx)
        score = (round(max(0.0, along) + weight * across, 3), offset)
        if best_score is None or score < best_score:
            best, best_score = key, score
    return best


def _gap(a: float, a_len: float, b: float, b_len: float) -> float:
    return max(0.0, max(a, b) - min(a + a_len, b + b_len))


@dataclass(eq=False)
class _Item:
    widget: Gtk.Widget
    group: str = ""
    on_activate: Callable[[], None] | None = None
    on_focus: Callable[[], None] | None = None
    on_blur: Callable[[], None] | None = None
    on_key: Callable[[str], bool] | None = None
    editable: bool = False


@dataclass
class _Scope:
    items: list[Gtk.Widget]
    on_back: Callable[[], bool] | None
    returns_to: Gtk.Widget | None
    entries: dict[str, Callable[[], Gtk.Widget | None]] = field(default_factory=dict)


class TvFocus:
    """The remote's focus over one TV stage (see the module docstring)."""

    def __init__(self, root: Gtk.Widget, *, on_back: Callable[[], bool] | None = None,
                 on_change: Callable[[Gtk.Widget | None], None] | None = None) -> None:
        self.root = root
        self.on_back = on_back
        self.on_change = on_change
        self._items: dict[Gtk.Widget, _Item] = {}
        self._entries: dict[str, Callable[[], Gtk.Widget | None]] = {}
        self._focused: Gtk.Widget | None = None
        self._scopes: list[_Scope] = []
        self._grab: Callable[[int, str, Gdk.ModifierType], bool] | None = None
        self._filters: list[Callable[[int, Gdk.ModifierType], bool]] = []
        self._controller = Gtk.EventControllerKey()
        self._controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._controller.connect("key-pressed", self._key_pressed)
        self._attached = None
        root.connect("map", lambda *_a: self._attach())
        if root.get_mapped():
            self._attach()

    # ── registration ────────────────────────────────────────────────────

    def add(self, widget: Gtk.Widget, *, group: str = "", on_activate=None, on_focus=None,
            on_blur=None, on_key=None, editable: bool = False) -> Gtk.Widget:
        """Make `widget` focusable by the remote.

        `on_key(direction)` returning True keeps an arrow. `editable=True` marks a
        text field (a Gtk.Entry or Gtk.Text): while it has the focus it holds GTK
        focus and receives typing, Left/Right, Backspace and Space itself, so a
        real keyboard and the system's on-screen keyboard type straight in; Up
        and Down still move, OK calls `on_activate`, Back steps out.
        """
        self._items[widget] = _Item(widget, group, on_activate, on_focus, on_blur, on_key, editable)
        # GTK focus follows the remote onto every item. That is what moves it off a
        # text field when the remote leaves one, so the system keyboard closes and
        # OK reaches the item instead of reopening the keyboard on the field.
        widget.set_focusable(True)
        return widget

    def remove(self, widget: Gtk.Widget) -> None:
        self._items.pop(widget, None)
        if self._focused is widget:
            self._set(None, notify=False)

    def clear(self, group: str | None = None) -> None:
        """Forget every item (or one group's), as a page rebuilds."""
        for widget in [w for w, item in self._items.items() if group is None or item.group == group]:
            self.remove(widget)

    def set_group_entry(self, group: str, entry: Callable[[], Gtk.Widget | None]) -> None:
        """Arriving in `group` from outside lands on `entry()` (the chosen item), not the nearest."""
        (self._scopes[-1].entries if self._scopes else self._entries)[group] = entry

    @property
    def focused(self) -> Gtk.Widget | None:
        return self._focused

    def group_of(self, widget: Gtk.Widget | None) -> str:
        item = self._items.get(widget) if widget is not None else None
        return item.group if item else ""

    def focus(self, widget: Gtk.Widget | None) -> None:
        """Move the one focus to `widget` (registered), calling its on_focus."""
        self._set(widget)

    def refresh(self) -> None:
        """Repaint the focus after the app rebuilt widgets (keeps the style class on the focused one)."""
        for widget in self._items:
            _mark(widget, widget is self._focused)

    # ── modal pieces ────────────────────────────────────────────────────

    def push_scope(self, widgets: Sequence[Gtk.Widget], *, on_back: Callable[[], bool] | None = None,
                   first: Gtk.Widget | None = None) -> None:
        """Confine the focus to `widgets` (a sheet) until `pop_scope()`; Back calls `on_back`."""
        self._scopes.append(_Scope(list(widgets), on_back, self._focused))
        target = first if first is not None else (widgets[0] if widgets else None)
        if target is not None:
            self._set(target)

    def set_scope_items(self, widgets: Sequence[Gtk.Widget]) -> None:
        """Replace the open scope's items (a list that filters as you type)."""
        if self._scopes:
            self._scopes[-1].items = list(widgets)

    def pop_scope(self) -> None:
        if not self._scopes:
            return
        scope = self._scopes.pop()
        back = scope.returns_to if scope.returns_to in self._items else None
        self._set(back)

    @property
    def in_scope(self) -> bool:
        return bool(self._scopes)

    def grab(self, handler: Callable[[int, str, Gdk.ModifierType], bool] | None) -> None:
        """Send every key to `handler(keyval, text, state)` first (the TV keyboard); None releases."""
        self._grab = handler

    @property
    def grabbed(self) -> bool:
        return self._grab is not None

    def add_filter(self, handler: Callable[[int, Gdk.ModifierType], bool]) -> None:
        """See each key before anything else (the idle hook); returning True swallows it."""
        self._filters.insert(0, handler)

    # ── keys ────────────────────────────────────────────────────────────

    def _attach(self) -> None:
        window = self.root.get_root()
        if window is None or window is self._attached:
            return
        if self._attached is not None:
            self._attached.remove_controller(self._controller)
        window.add_controller(self._controller)
        self._attached = window

    def _key_pressed(self, controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if not self.root.get_mapped():
            return False
        for handler in list(self._filters):
            if handler(keyval, state):
                return True
        if self._grab is not None:
            unicode = Gdk.keyval_to_unicode(keyval)
            text = chr(unicode) if unicode and unicode >= 0x20 and unicode != 0x7f else ""
            if self._grab(keyval, text, state):
                return True
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        return self.handle(keyval)

    def handle(self, keyval: int) -> bool:
        """Act on one remote key as if pressed (tests, scripted reviews)."""
        direction = direction_for(keyval)
        item = self._items.get(self._focused) if self._focused is not None else None
        if item is not None and item.editable:
            if direction in (LEFT, RIGHT) or keyval in (Gdk.KEY_BackSpace, Gdk.KEY_space):
                return False
            if direction is None and keyval not in OK_KEYS and keyval not in BACK_KEYS:
                return False
        if direction is not None:
            if item is not None and item.on_key is not None and item.on_key(direction):
                return True
            self.move(direction)
            return True
        if keyval in OK_KEYS:
            if keyval == Gdk.KEY_space and item is None:
                return False
            if item is not None and item.on_activate is not None:
                item.on_activate()
                return True
            return keyval != Gdk.KEY_space
        if keyval in BACK_KEYS:
            if self._scopes:
                scope = self._scopes[-1]
                if scope.on_back is None or scope.on_back():
                    if self._scopes and self._scopes[-1] is scope:
                        self.pop_scope()
                return True
            return bool(self.on_back and self.on_back())
        return False

    def move(self, direction: str) -> bool:
        """Move the focus one step spatially; returns whether it moved."""
        current = self._focused
        candidates = self._candidates()
        if current is None or current not in candidates:
            first = next(iter(candidates), None)
            if first is not None:
                self._set(first)
            return first is not None
        origin = self._rect(current)
        if origin is None:
            return False
        rects = [(w, r) for w in candidates if w is not current and (r := self._rect(w)) is not None]
        target = nearest(origin, rects, direction)
        if target is None:
            return False
        group = self.group_of(target)
        entries = self._scopes[-1].entries if self._scopes else self._entries
        if group and group != self.group_of(current) and group in entries:
            entered = entries[group]()
            if entered is not None and entered in self._items:
                target = entered
        self._set(target)
        return True

    def _candidates(self) -> list[Gtk.Widget]:
        pool = self._scopes[-1].items if self._scopes else list(self._items)
        return [w for w in pool if w in self._items and w.get_mapped() and w.get_sensitive()
                and w.get_opacity() > 0.01]

    def _rect(self, widget: Gtk.Widget) -> Rect | None:
        ok, bounds = widget.compute_bounds(self.root)
        if not ok:
            return None
        return (bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height())

    def _set(self, widget: Gtk.Widget | None, *, notify: bool = True) -> None:
        previous = self._focused
        if widget is previous:
            if widget is not None:
                _mark(widget, True)
            return
        self._focused = widget
        if previous is not None:
            _mark(previous, False)
            item = self._items.get(previous)
            if item is not None and item.on_blur is not None and notify:
                item.on_blur()
        if widget is not None:
            _mark(widget, True)
            widget.grab_focus()
            item = self._items.get(widget)
            if item is not None and item.on_focus is not None and notify:
                item.on_focus()
        if notify and self.on_change is not None:
            self.on_change(widget)


def _mark(widget: Gtk.Widget, on: bool) -> None:
    if on:
        widget.add_css_class("tv-focus")
    else:
        widget.remove_css_class("tv-focus")
    lift = widget if hasattr(widget, "set_lifted") else None
    if lift is not None:
        lift.set_lifted(on)
    setter = getattr(widget, "set_tv_focus", None)
    if setter is not None:
        setter(on)
tv_nearest = nearest  # the public name (luma_appkit.tv_nearest)
