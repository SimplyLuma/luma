# SPDX-License-Identifier: Apache-2.0
"""Apps (handoff 5): categories as photo tiles, Top apps, Recently added and updated; one category or
the filters narrow it to one list."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..model import rules
from ..model.world import FORMS
from .base import Page, intro
from .menus import choice_button, filters_button

SEALS = (("luma", "Luma"), ("verified", "Verified developers"), ("community", "Community"))


class AppsPage(Page):
    place = "apps"

    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.narrowing = rules.Narrowing()

    def reset(self) -> None:
        self.narrowing = rules.Narrowing()

    def snapshot(self) -> dict:
        n = self.narrowing
        return {"category": n.category, "works": list(n.works), "seals": list(n.seals)}

    def restore(self, state: dict) -> None:
        self.narrowing = rules.Narrowing(state.get("category", ""), list(state.get("works", ())),
                                         list(state.get("seals", ())))

    def set_category(self, key: str) -> None:
        self.narrowing.category = key
        self.shell.refresh_body()

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-apps")
        if self.narrowing.active:
            title, line = rules.narrowed_line(self.world, self.narrowing)
            page.append(intro(title, line))
        else:
            page.append(intro("Apps", "For every screen you own"))
        return page

    def bar_items(self) -> list:
        world = self.world
        category = choice_button(
            lambda: (world.category(self.narrowing.category).name if self.narrowing.category else "All apps"),
            "layout-grid", "Category",
            lambda: [("", "All apps")] + [(c.key, c.name) for c in world.categories],
            lambda: self.narrowing.category, self.set_category, name="dq-catmenu")
        self.category_button = category.widget

        def toggled(group: str, key: str, on: bool) -> None:
            target = self.narrowing.works if group == "Also works on" else self.narrowing.seals
            if on and key not in target:
                target.append(key)
            elif not on and key in target:
                target.remove(key)
            self.shell.refresh_body()

        filters = filters_button(
            lambda: [("Also works on", [(f.id, f.name, f.icon, f.id in self.narrowing.works) for f in FORMS]),
                     ("Made by", [(k, n, None, k in self.narrowing.seals) for k, n in SEALS])],
            toggled, lambda: rules.filter_count(self.narrowing), name="dq-filters")
        self.filters_button = filters.widget
        return [category, filters]

    # screenshot states (tests/redesign_shot.py --demo)
    def demo_category(self) -> None:
        self.category_button.emit("clicked")

    def demo_filters(self) -> None:
        self.filters_button.emit("clicked")
        self.narrowing.works.append("phone")
        self.shell.refresh_body()
