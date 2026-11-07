# SPDX-License-Identifier: Apache-2.0
"""Tweaks (handoff 7): cards with Install, never a switch."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..parts.grid import Grid
from ..parts.rows import tweak_card
from .base import Page, intro
from .common import empty, install_here, opener
from .menus import choice_button

SHOW = (("", "All tweaks"), ("on", "On here"), ("off", "Off here"))


class TweaksPage(Page):
    place = "tweaks"

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
        page.add_css_class("p-tweaks")
        page.append(intro("Tweaks", "Small changes to how Luma works. Turn one off and things go back exactly "
                                    "as they were."))
        shell, world = self.shell, self.world
        items = [it for it in world.items("tweak")
                 if not self.show or (self.show == "on") == (world.here in world.on_devices(it))]
        cards = Grid(lambda w: 1 if shell.is_compact() or w < 640 else 2, column_gap=12, row_gap=12, css="dq-tgrid")
        for item in items:
            cards.append(tweak_card(world, item, on_open=opener(shell, "tweak", item.id),
                                    on_install=install_here(shell, "tweak", item.id)))
        page.append(cards if items else empty())
        return page

    def bar_items(self) -> list:
        return [choice_button(lambda: dict(SHOW)[self.show], "sliders-horizontal", "Show", lambda: SHOW,
                              lambda: self.show, self.pick, name="dq-tweakmenu")]
