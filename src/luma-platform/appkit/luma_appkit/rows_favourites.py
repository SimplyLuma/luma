# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: FavouritesStrip, the favourites above a sidebar's list (SB2).

    FavouritesStrip([("Priya", "Priya Raman", None), ("Mum", "Mum", None)], on_open=call)       # Phone
    FavouritesStrip([Favourite("Home", icon="house", sub="12 min"),
                     Favourite("Work", icon="briefcase", sub="21 min")], kind="places", on_open=go)  # Maps

v70 Phone `.pnfavs`: 64 px tiles, a 48 px face over the name at 11.5/600.
Maps `.mpfavs`: 80 px tiles, a 44 px round glyph well in the accent over the
name and an 11 px sub (the drive time). Tiles sit in one row, equal width; the
strip is as wide as its sidebar. Arrows move between tiles; Enter opens one.

A tuple is `(label, person-or-icon, sub)`: a person's name for
`kind="people"`, a Lucide glyph for `kind="places"`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from . import icons, rows_tokens  # noqa: E402

__all__ = ["FAVOURITE_KINDS", "Favourite", "FavouritesStrip"]

FAVOURITE_KINDS = ("people", "places")


@dataclass(frozen=True)
class Favourite:
    """One favourite: its label, and a person (name, picture) or a glyph, and an optional sub line."""

    label: str
    person: str | None = None
    icon: str | None = None
    sub: str | None = None
    picture: Gdk.Paintable | None = None
    key: object = None


class FavouritesStrip(Gtk.Grid):
    """A row of favourites above a list: people's faces or places' glyphs, each a button."""

    __gtype_name__ = "LumaUIFavouritesStrip"

    def __init__(self, items: Sequence[Favourite | tuple], *, kind: str = "people",
                 on_open: Callable[[Favourite], None] | None = None, label: str = "Favourites",
                 heading: str | None = None) -> None:
        if kind not in FAVOURITE_KINDS:
            raise ValueError(f"FavouritesStrip kind must be one of {', '.join(FAVOURITE_KINDS)}, not {kind!r}")
        super().__init__(accessible_role=Gtk.AccessibleRole.LIST)
        self.add_css_class("lumaui-favourites")
        self.add_css_class(kind)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.kind, self.on_open, self.heading = kind, on_open, heading
        if heading:
            self.add_css_class("headed")
        self.buttons: list[Gtk.Button] = []
        self.items: list[Favourite] = []
        self.set_items(items)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def _favourite(self, item: Favourite | tuple) -> Favourite:
        if isinstance(item, Favourite):
            return item
        label, lead, *rest = item
        sub = rest[0] if rest else None
        if self.kind == "people":
            return Favourite(label, person=lead, sub=sub)
        return Favourite(label, icon=lead, sub=sub)

    def set_items(self, items: Sequence[Favourite | tuple]) -> None:
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.remove(child)
            child = following
        self.items = [self._favourite(item) for item in items]
        self.buttons = []
        # v70: a grid of 4 (people) or 3 (places) equal columns, so a short
        # strip keeps its tiles' width instead of stretching them.
        wide = self.kind == "places"
        columns = int(rows_tokens.value("favourites", "wide_column_count" if wide else "column_count"))
        gap = int(rows_tokens.value("favourites", "wide_gap" if wide else "gap"))
        self.set_column_homogeneous(True)
        self.set_column_spacing(gap)
        self.set_row_spacing(gap)
        row_offset = 1 if self.heading else 0
        if self.heading:
            heading = Gtk.Label(label=self.heading, xalign=0, accessible_role=Gtk.AccessibleRole.HEADING)
            heading.add_css_class("lumaui-favourites-heading")
            self.attach(heading, 0, 0, columns, 1)
        for index in range(len(self.items), columns):
            self.attach(Gtk.Box(accessible_role=Gtk.AccessibleRole.PRESENTATION), index, row_offset, 1, 1)
        for index, item in enumerate(self.items):
            button = Gtk.Button(accessible_role=Gtk.AccessibleRole.LIST_ITEM, hexpand=True)
            button.add_css_class("lumaui-favourite")
            if self.kind == "places":
                button.add_css_class("wide")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
            if self.kind == "people":
                from .content_cards import PersonAvatar
                face = PersonAvatar(item.person or item.label, int(rows_tokens.value("favourites", "face")),
                                    picture=item.picture)
            else:
                face = Gtk.CenterBox(halign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
                face.add_css_class("lumaui-favourite-well")
                face.set_center_widget(icons.image(item.icon or "star"))
            box.append(face)
            name = Gtk.Label(label=item.label, max_width_chars=9, ellipsize=Pango.EllipsizeMode.END)
            name.add_css_class("lumaui-favourite-name")
            box.append(name)
            if item.sub:
                sub = Gtk.Label(label=item.sub)
                sub.add_css_class("lumaui-favourite-sub")
                box.append(sub)
            button.set_child(box)
            spoken = item.label + (f", {item.sub}" if item.sub else "")
            button.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
            button.set_tooltip_text(item.person if item.person and item.person != item.label else None)
            button.connect("clicked", lambda _b, it=item: self.on_open(it) if self.on_open else None)
            self.attach(button, index % columns, index // columns + row_offset, 1, 1)
            self.buttons.append(button)
        self.set_visible(bool(self.items))

    def _key(self, _controller, keyval: int, _code: int, _state: object) -> bool:
        if keyval not in (Gdk.KEY_Left, Gdk.KEY_Right) or not self.buttons:
            return False
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        index = next((i for i, b in enumerate(self.buttons) if focus is b or (focus and focus.is_ancestor(b))), -1)
        step = 1 if keyval == Gdk.KEY_Right else -1
        if self.get_direction() == Gtk.TextDirection.RTL:
            step = -step
        target = index + step
        if 0 <= target < len(self.buttons):
            self.buttons[target].grab_focus()
        return True
