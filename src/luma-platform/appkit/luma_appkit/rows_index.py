# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: the A–Z index (v71 `lAZ`, "LumaUI: A–Z index", phone).

Long alphabetical lists (Contacts, Phone's contacts) get A to Z down the right
edge. Letters that have no one are dimmed. Touch or drag along it and the list
jumps to the letter (or the next one that has someone), which shows large in a
bubble beside the thumb. The list stops short of the index: the scroller gets
a 26 px lane on its right (v71 `.lazgut`), so rows, cards and hairlines never
run under the letters.

It shows only at phone width (the window under 560) and only when the list has
six or more letters; otherwise it hides itself and gives the lane back.

Put it over the list's scroller, in any Gtk.Overlay above it: it places itself
as v71 measures it, from the window (150 from its top and bottom, 3 from its
right edge, clipped to the overlay), wherever that overlay sits:

    index = AZIndex(contacts_listbox, key=lambda row: row.person.sort_name)
    overlay.add_overlay(index)          # it places itself at the end edge
    index.refresh()                     # after repopulating (a Gtk.ListBox's children are watched anyway)

`list_view` is a `Gtk.ListBox` (`key` gets each row) or a `Gtk.ListView`
(`key` gets each model item). `scroller` defaults to the list's
`Gtk.ScrolledWindow` ancestor.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

__all__ = ["AZIndex", "AZ_LETTERS"]

AZ_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PHONE_WIDTH = 560
MINIMUM = 6          # fewer letters than this: no index (v71 `heads.length < 6`)
TOP = BOTTOM = 150  # from the window's top and bottom edges (v71 .lazidx), wherever the overlay sits
END = 3             # from the window's right edge (v71 phone .lazidx { right: 3px })
BUBBLE = 56
BUBBLE_GAP = 40      # the bubble's right edge sits this far left of the index's right edge
LIFT = 4             # the heading lands this far under the top (v71 `- 4`)


def _letter(text: str | None) -> str:
    text = (text or "").strip()
    return text[:1].upper() if text else ""


class AZIndex(Gtk.Widget):
    """A to Z down the end edge of an alphabetical list; touch or drag to jump."""

    __gtype_name__ = "LumaUIAZIndex"

    def __init__(self, list_view: Gtk.ListBox | Gtk.ListView, *, key: Callable[[object], str],
                 scroller: Gtk.ScrolledWindow | None = None, phone_only: bool = True) -> None:
        super().__init__(halign=Gtk.Align.END, valign=Gtk.Align.FILL)
        self.set_child_visible(False)
        self.add_css_class("lumaui-az-index")
        self.set_accessible_role(Gtk.AccessibleRole.NAVIGATION)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Jump to a letter"])
        self.list_view, self.key, self.phone_only = list_view, key, phone_only
        self._scroller = scroller
        self._heads: list[tuple[str, object]] = []   # (letter, the row, or the model position)
        self._phone = not phone_only
        self._pending = 0
        self.letters: dict[str, Gtk.Label] = {}
        for letter in AZ_LETTERS:
            label = Gtk.Label(label=letter, css_classes=["lumaui-az-letter"], can_target=False)
            label.set_parent(self)
            self.letters[letter] = label
        self.bubble = Gtk.Label(css_classes=["lumaui-az-bubble"], can_target=False)
        self.bubble.set_parent(self)
        self.bubble.set_child_visible(False)
        self._bubble_y = 0.0

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", lambda *_a: self.let_go())
        drag.connect("cancel", lambda *_a: self.let_go())
        self.add_controller(drag)
        self._start_y = 0.0

        if isinstance(list_view, Gtk.ListBox):
            self._children = list_view.observe_children()
            self._children.connect("items-changed", lambda *_a: self.schedule())
        else:
            self._watch_model()
            list_view.connect("notify::model", lambda *_a: (self._watch_model(), self.schedule()))
        if phone_only:
            from .structure_adapt import WidthWatch
            self._watch = WidthWatch(list_view, self._width_changed, threshold=PHONE_WIDTH - 1)
        self.connect("map", lambda *_a: self.schedule())
        self.connect("notify::visible", self._sync_gutter)
        self._overlay: Gtk.Overlay | None = None
        self._overlay_handler = 0
        self.connect("notify::parent", self._reparented)
        self.schedule()

    # ── where it sits ──────────────────────────────────────────────────────
    def _reparented(self, *_args) -> None:
        if self._overlay is not None and self._overlay_handler:
            self._overlay.disconnect(self._overlay_handler)
        self._overlay, self._overlay_handler = None, 0
        parent = self.get_parent()
        if isinstance(parent, Gtk.Overlay):
            self._overlay = parent
            self._overlay_handler = parent.connect("get-child-position", self._position)

    def band(self, overlay: Gtk.Widget) -> tuple[int, int, int, int] | None:
        """The index's box in `overlay`'s coordinates: 150 from the window's top and bottom, 3 from its
        right, clipped to the overlay (v71 positions .lazidx in the window, not in the list)."""
        root = overlay.get_root()
        if not isinstance(root, Gtk.Widget):
            return None
        ok, origin = overlay.compute_point(root, Graphene.Point().init(0, 0))
        if not ok:
            return None
        width = max(self.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 1)
        window_w, window_h = root.get_width(), root.get_height()
        top = max(0, TOP - origin.y)
        bottom = min(overlay.get_height(), window_h - BOTTOM - origin.y)
        right = min(overlay.get_width(), window_w - END - origin.x)
        if bottom - top < 26:
            return None
        return round(right - width), round(top), width, round(bottom - top)

    def _position(self, overlay: Gtk.Overlay, widget: Gtk.Widget, allocation: Gdk.Rectangle) -> bool:
        if widget is not self:
            return False
        box = self.band(overlay)
        if box is None:
            return False
        allocation.x, allocation.y, allocation.width, allocation.height = box
        return True

    # ── what the list holds ────────────────────────────────────────────────
    def _watch_model(self) -> None:
        model = self.list_view.get_model() if isinstance(self.list_view, Gtk.ListView) else None
        if model is not None and getattr(self, "_model", None) is not model:
            self._model = model
            model.connect("items-changed", lambda *_a: self.schedule())

    def _width_changed(self, width: int) -> None:
        self._phone = 0 < width < PHONE_WIDTH
        self.refresh()

    def schedule(self) -> None:
        if not self._pending:
            self._pending = GLib.idle_add(self._idle)

    def _idle(self) -> bool:
        self._pending = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    @property
    def scroller(self) -> Gtk.ScrolledWindow | None:
        if self._scroller is None:
            found = self.list_view.get_ancestor(Gtk.ScrolledWindow)
            return found if isinstance(found, Gtk.ScrolledWindow) else None
        return self._scroller

    def headings(self) -> list[tuple[str, object]]:
        """The first entry of each letter, in list order: (letter, its row or model position)."""
        heads: list[tuple[str, object]] = []
        seen: set[str] = set()
        if isinstance(self.list_view, Gtk.ListBox):
            row = self.list_view.get_first_child()
            while row is not None:
                if isinstance(row, Gtk.ListBoxRow) and row.get_visible():
                    letter = _letter(self.key(row))
                    if letter and letter in AZ_LETTERS and letter not in seen:
                        seen.add(letter)
                        heads.append((letter, row))
                row = row.get_next_sibling()
        else:
            model = self.list_view.get_model()
            for position in range(model.get_n_items() if model is not None else 0):
                letter = _letter(self.key(model.get_item(position)))
                if letter and letter in AZ_LETTERS and letter not in seen:
                    seen.add(letter)
                    heads.append((letter, position))
        return heads

    def refresh(self) -> None:
        """Re-read the list: which letters have someone, and whether the index shows at all."""
        self._heads = self.headings()
        have = {letter for letter, _ in self._heads}
        for letter, label in self.letters.items():
            (label.remove_css_class if letter in have else label.add_css_class)("none")
        shown = (self._phone or not self.phone_only) and len(self._heads) >= MINIMUM
        # Eligibility belongs to the component; visible belongs to its caller.
        # A scheduled refresh must not resurrect an index over a detail page.
        self.set_child_visible(shown)
        self._sync_gutter()

    def _sync_gutter(self, *_args) -> None:
        shown = self.get_visible() and self.get_child_visible()
        scroller = self.scroller
        if scroller is not None:
            (scroller.add_css_class if shown else scroller.remove_css_class)("lumaui-az-gutter")

    # ── touch ──────────────────────────────────────────────────────────────
    def _begin(self, gesture: Gtk.GestureDrag, _x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._start_y = y
        self.jump_at(y)

    def _update(self, _gesture: Gtk.GestureDrag, _dx: float, dy: float) -> None:
        self.jump_at(self._start_y + dy)

    def letter_at(self, y: float) -> str:
        """The letter under `y` (the index's own coordinates), clamped to A–Z."""
        height = max(1, self.get_height())
        return AZ_LETTERS[max(0, min(25, int(y / height * 26)))]

    def target_for(self, letter: str) -> tuple[str, object] | None:
        """The heading a letter jumps to: its own, or the next one that has someone, or the last."""
        for head in self._heads:
            if head[0] >= letter:
                return head
        return self._heads[-1] if self._heads else None

    def jump_at(self, y: float) -> str | None:
        """Jump the list to the letter under `y` and show the bubble there. Returns the letter landed on."""
        head = self.target_for(self.letter_at(y))
        if head is None:
            return None
        self._scroll_to(head[1])
        self.bubble.set_label(head[0])
        self._bubble_y = y
        self.bubble.set_child_visible(True)
        self.add_css_class("on")
        self.queue_allocate()
        return head[0]

    def let_go(self) -> None:
        self.bubble.set_child_visible(False)
        self.remove_css_class("on")

    def _scroll_to(self, target: object) -> None:
        if isinstance(target, int):
            self.list_view.scroll_to(target, Gtk.ListScrollFlags.NONE, None)
            return
        scroller = self.scroller
        content = scroller.get_child() if scroller is not None else None
        if isinstance(content, Gtk.Viewport):
            content = content.get_child()
        if scroller is None or content is None:
            return
        row = target.get_header() or target if isinstance(target, Gtk.ListBoxRow) else target
        ok, top = row.compute_point(content, Graphene.Point().init(0, 0))
        first = content.get_first_child()
        ok_first, first_top = (first.compute_point(content, Graphene.Point().init(0, 0))
                               if first is not None else (True, Graphene.Point().init(0, 0)))
        if not (ok and ok_first):
            return
        adjustment = scroller.get_vadjustment()
        value = top.y - first_top.y - LIFT
        adjustment.set_value(max(adjustment.get_lower(), min(value, adjustment.get_upper() - adjustment.get_page_size())))

    # ── layout ─────────────────────────────────────────────────────────────
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        if orientation == Gtk.Orientation.HORIZONTAL:
            width = max(label.measure(orientation, -1)[1] for label in self.letters.values())
            return width, width, -1, -1
        height = sum(label.measure(orientation, -1)[1] for label in self.letters.values())
        return 0, height, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        # space-between: the first letter at the top, the last at the bottom, the rest evenly between
        sizes = [label.measure(Gtk.Orientation.VERTICAL, width)[1] for label in self.letters.values()]
        spare = max(0, height - sum(sizes))
        gap = spare / 25
        y = 0.0
        for label, size in zip(self.letters.values(), sizes):
            label.allocate(width, size, -1, Gsk.Transform().translate(Graphene.Point().init(0, round(y))))
            y += size + gap
        if self.bubble.get_child_visible():
            # beside the thumb: the bubble's right edge 40 left of the index's right edge
            x = width - BUBBLE_GAP - BUBBLE
            self.bubble.allocate(BUBBLE, BUBBLE, -1,
                                 Gsk.Transform().translate(Graphene.Point().init(x, self._bubble_y - BUBBLE / 2)))

    def do_dispose(self) -> None:
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0
        for child in (*self.letters.values(), self.bubble):
            if child.get_parent() is self:
                child.unparent()
        self.letters = {}
        super().do_dispose()
