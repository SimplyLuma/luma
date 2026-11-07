# SPDX-License-Identifier: Apache-2.0
"""Looks (handoff 6): Themes first, then Wallpapers, then Icons, sounds and type."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..parts.grid import Grid
from ..parts.rows import look_tile
from .base import Page, intro, section
from .common import empty, opener
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

    def _shown(self, item) -> bool:
        kind = item.look.kind
        if self.show == "type":
            return kind in ("Font", "Cursor", "Appearance")
        return not self.show or kind == self.show

    def build(self) -> Gtk.Widget:
        shell, world = self.shell, self.world
        compact = shell.is_compact()
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-looks")
        page.append(intro("Looks", "Themes, wallpapers, icons, sounds and type"))
        here = world.this_device.form
        shown = [it for it in world.items("look") if here in it.runs and self._shown(it)]
        parts = (("Themes", [it for it in shown if it.look.kind == "Theme"], 3),
                 ("Wallpapers", [it for it in shown if it.look.kind == "Wallpaper"], 4),
                 ("Icons, sounds and type", [it for it in shown if it.look.kind not in ("Theme", "Wallpaper")], 4))
        for title, items, columns in parts:
            if not items:
                continue
            grid = Grid(lambda w, c=columns: 2 if compact or w < 640 else c, column_gap=16, row_gap=22,
                        css="dq-lgrid" + (" big" if columns == 3 else ""))
            for item in items:
                grid.append(look_tile(world, item, on_open=opener(shell, "look", item.id),
                                      in_use=world.here in world.on_devices(item)))
            page.append(section(title, grid))
        if not shown:
            page.append(empty())
        return page

    def bar_items(self) -> list:
        return [choice_button(lambda: dict(SHOW)[self.show], "palette", "Show", lambda: SHOW, lambda: self.show,
                              self.pick, name="dq-lookmenu")]
