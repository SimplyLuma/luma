# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: count badge and category pill.

CountBadge — how many, as a pill with tabular numbers. A total is shown
exactly with a thousands separator (1,284); `attention=True` (unread, needs
you) takes the accent and caps at 99+. Nothing is shown for 0. Never in
brackets or beside a dot. v70 `lCount(n, accent)`.

CategoryPill — Depot's five categories (Create, Work, Media, Play, Tools) in
their own colour wherever a category shows, light and dark, text at
6.2–8.2:1. The colour comes from the category, never from the application.
v70 `lCat(key)`.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402

__all__ = ["CountBadge", "CategoryPill", "count_text", "CATEGORIES"]

#: The category keys, in Depot's order.
CATEGORIES = tokens.CATEGORY_ORDER


def count_text(count: int, attention: bool = False) -> str:
    """What a count badge says: "" for 0, "1,284" for a total, "99+" past the attention cap."""
    count = int(count)
    if count <= 0:
        return ""
    if attention and count > tokens.COUNT_ATTENTION_CAP:
        return f"{tokens.COUNT_ATTENTION_CAP}+"
    return f"{count:,}".replace(",", tokens.COUNT_SEPARATOR)


class CountBadge(Gtk.Label):
    """A count as a pill: `CountBadge(1284)` is a total, `CountBadge(3, attention=True)` is unread."""

    __gtype_name__ = "LumaUICountBadge"

    def __init__(self, count: int = 0, *, attention: bool = False) -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.END, xalign=0.5)
        self.add_css_class("lumaui-count")
        self._count = 0
        self._attention = False
        self.set_attention(attention)
        self.set_count(count)

    @GObject.Property(type=int, default=0)
    def count(self) -> int:
        return self._count

    @count.setter
    def count(self, value: int) -> None:
        self.set_count(value)

    @GObject.Property(type=bool, default=False)
    def attention(self) -> bool:
        return self._attention

    @attention.setter
    def attention(self, value: bool) -> None:
        self.set_attention(value)

    def set_count(self, count: int) -> None:
        self._count = max(0, int(count))
        self._refresh()

    def set_attention(self, attention: bool) -> None:
        self._attention = bool(attention)
        (self.add_css_class if self._attention else self.remove_css_class)("attention")
        self._refresh()

    def _refresh(self) -> None:
        text = count_text(self._count, self._attention)
        self.set_label(text)
        self.set_visible(bool(text))
        # A capped badge still reads the real number.
        exact = f"{self._count:,}".replace(",", tokens.COUNT_SEPARATOR)
        self.update_property([Gtk.AccessibleProperty.LABEL], [exact])


class CategoryPill(Gtk.Label):
    """`CategoryPill("media")`: the category's name in the category's colour."""

    __gtype_name__ = "LumaUICategoryPill"

    def __init__(self, category: str) -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.add_css_class("lumaui-category")
        self._category = ""
        self.set_category(category)

    @property
    def category(self) -> str:
        return self._category

    def set_category(self, category: str) -> None:
        key = str(category).strip().lower()
        if self._category:
            self.remove_css_class(self._category)
        # An unknown category keeps its own name and the neutral Work hue,
        # as the design does; it never takes a colour from the caller.
        self._category = key if key in tokens.CATEGORY_LABELS else "work"
        self.add_css_class(self._category)
        self.set_label(tokens.CATEGORY_LABELS.get(key, str(category).strip().title()))
