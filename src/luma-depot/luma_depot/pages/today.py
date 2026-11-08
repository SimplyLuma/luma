# SPDX-License-Identifier: Apache-2.0
"""Today (handoff 4): the one editors' story, Popular beside New and updated, then Looks and Tweaks.
Only the story is chosen by a person; the rest builds itself from the catalogue."""
from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Inset

from ..model import rules
from ..parts.grid import Grid
from ..parts.rows import look_tile, rank_row, tweak_card
from ..parts.story import story_card
from ..pictures.frames import shot_file
from .base import Page, kicker_title, section
from .common import install_here, opener


class TodayPage(Page):
    place = "today"

    def build(self) -> Gtk.Widget:
        shell, world = self.shell, self.world
        compact = shell.is_compact()
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-today")
        page.append(kicker_title(date.today().strftime("%A, %B %-d"), "Today"))
        story = world.story
        if story is not None and world.item("app", story.app) is not None:
            item = world.item("app", story.app)
            here = world.here in world.on_devices(item)
            art = (world.media_path(story.art) if "://" in story.art else
                   str(world.assets / "art" / f"{story.art}.webp") if world.assets and story.art else None)
            shot = next((shot_file(world, k) for k in item.app.shot_keys if "desktop-dark" in k), None)
            page.append(story_card(world, story, art=art, shot=shot, here=here, compact=compact,
                                   on_open=opener(shell, "app", item.id),
                                   on_action=(lambda: None) if here else install_here(shell, "app", item.id)))
        popular = Inset()
        for rank, item in enumerate(rules.today_popular(world), 1):
            popular.append(rank_row(world, item, " · ".join(x for x in (rules.category_name(world, item),
                                                                         rules.rating_line(item)) if x),
                                    rank=rank if world.popular_measured else None,
                                    on_open=opener(shell, "app", item.id),
                                    on_install=install_here(shell, "app", item.id)))
        fresh = Inset()
        for item in rules.today_fresh(world):
            fresh.append(rank_row(world, item, " · ".join(x for x in (rules.category_name(world, item),
                                                                       rules.short_date(item.app.updated)) if x),
                                  rank=None, on_open=opener(shell, "app", item.id),
                                  on_install=install_here(shell, "app", item.id)))
        two = Grid(lambda w: 1 if compact or w < 640 else 2, column_gap=32, row_gap=0, css="dq-two")
        two.append(section(rules.popular_title(world, "Popular"), popular, more=("See all", lambda: shell.go("apps"))))
        two.append(section("New and updated", fresh, follows=True))
        page.append(two)
        looks = sorted(world.items("look"), key=lambda it: it.users, reverse=True)
        looks = [it for it in looks if world.this_device.form in it.runs][:4]
        grid = Grid(lambda w: 2 if compact or w < 640 else 4, column_gap=16, row_gap=22, css="dq-lgrid")
        for item in looks:
            grid.append(look_tile(world, item, on_open=opener(shell, "look", item.id),
                                  in_use=world.here in world.on_devices(item)))
        page.append(section("Looks", grid, more=("See all", lambda: shell.go("looks")), follows=True))
        tweaks = sorted(world.items("tweak"), key=lambda it: it.users, reverse=True)
        tweaks = [it for it in tweaks if world.this_device.form in it.runs][:4]
        cards = Grid(lambda w: 1 if compact or w < 640 else 2, column_gap=12, row_gap=12, css="dq-tgrid")
        for item in tweaks:
            cards.append(tweak_card(world, item, on_open=opener(shell, "tweak", item.id),
                                    on_install=install_here(shell, "tweak", item.id)))
        page.append(section("Tweaks", cards, more=("See all", lambda: shell.go("tweaks")), follows=True))
        return page
