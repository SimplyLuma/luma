# SPDX-License-Identifier: Apache-2.0
"""Apps (handoff 5): categories as photo tiles, Top apps, Recently added and updated; one category or
the filters narrow it to one list."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import TwoColumnInset, icons

from ..model import rules
from ..model.world import FORMS
from ..parts.grid import Grid
from ..parts.rows import category_tile, rank_row
from .base import Page, intro, label, section
from .common import empty, install_here, opener
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

    def _ranks(self, items, second, *, ranked: bool) -> Gtk.Widget:
        shell, world = self.shell, self.world
        inset = TwoColumnInset(columns=1 if shell.is_compact() else 2)
        for index, item in enumerate(items, 1):
            inset.append(rank_row(world, item, second(item), rank=index if ranked else None,
                                  on_open=opener(shell, "app", item.id), on_install=install_here(shell, "app", item.id)))
        return inset

    def build(self) -> Gtk.Widget:
        shell, world = self.shell, self.world
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-apps")
        category_of = lambda it: world.category(it.category).name if world.category(it.category) else ""  # noqa: E731
        if self.narrowing.active:
            crumb = Gtk.Button(halign=Gtk.Align.START)
            crumb.add_css_class("dq-link")
            crumb.add_css_class("dq-crumb")
            line = Gtk.Box()
            line.append(icons.image("chevron-left"))
            line.append(Gtk.Label(label="All apps"))
            crumb.set_child(line)
            crumb.connect("clicked", lambda _b: self._clear_category())
            page.append(crumb)
            title, words = rules.narrowed_line(world, self.narrowing)
            page.append(intro(title, words))
            items = rules.narrowed(world, self.narrowing)
            ranks = self._ranks(items, lambda it: it.line, ranked=False) if items else empty()
            ranks.add_css_class("dq-ranks-after")
            page.append(ranks)
            return page
        page.append(intro("Apps", "For every screen you own"))
        tiles = Grid(lambda w: 2 if shell.is_compact() or w < 640 else 5, column_gap=12, row_gap=12, css="dq-cats")
        for category in world.categories:
            tiles.append(category_tile(world, category, on_open=lambda k=category.key: self.set_category(k)))
        page.append(tiles)
        page.append(section(rules.popular_title(world, "Top apps"), self._ranks(rules.top_apps(world), lambda it: " · ".join(
            x for x in (category_of(it), rules.rating_line(it)) if x), ranked=world.popular_measured)))
        page.append(section("Recently added and updated", follows=True, body=self._ranks(
            rules.recently_updated(world), lambda it: " · ".join(
                x for x in (category_of(it), rules.short_date(it.app.updated)) if x), ranked=False)))
        return page

    def _clear_category(self) -> None:
        self.narrowing.category = ""
        if getattr(self, "category_button", None) is not None:
            self.category_button.set_label_text("All apps")
        self.shell.refresh_body()

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

    def demo_create(self) -> None:
        self.narrowing.category = "create"
        if getattr(self, "category_button", None) is not None:
            self.category_button.set_label_text("Create")
        self.shell.refresh_body()

    def demo_filters(self) -> None:
        self.filters_button.emit("clicked")
        self.narrowing.works.append("phone")
        self.shell.refresh_body()
