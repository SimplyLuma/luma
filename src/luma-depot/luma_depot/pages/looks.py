# SPDX-License-Identifier: Apache-2.0
"""Looks (handoff 6): Themes first, then Wallpapers, then Icons, sounds and type."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .base import Page, intro
from .menus import choice_button

#: The Show menu (2.4): key, words.
SHOW = (("", "All looks"), ("Wallpaper", "Wallpapers"), ("Theme", "Themes"), ("Icons", "Icons"),
        ("Sounds", "Sounds"), ("type", "Type and pointers"))


class LooksPage(Page):
    place = "looks"

    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.show = ""

    def reset(self) -> None:
        self.show = ""

    def snapshot(self) -> dict:
        return {"show": self.show}

    def restore(self, state: dict) -> None:
        self.show = state.get("show", "")

    def pick(self, key: str) -> None:
        self.show = key
        self.shell.refresh_body()

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-looks")
        page.append(intro("Looks", "Themes, wallpapers, icons, sounds and type"))
        return page

    def bar_items(self) -> list:
        return [choice_button(lambda: dict(SHOW)[self.show], "palette", "Show", lambda: SHOW, lambda: self.show,
                              self.pick, name="dq-lookmenu")]
