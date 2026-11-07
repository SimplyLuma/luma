# SPDX-License-Identifier: Apache-2.0
"""Library (handoff 10): an icon grid of everything you have, one line of state each."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import BarWidget, Button

from .base import Page, intro
from .menus import choice_button


class LibraryPage(Page):
    place = "library"

    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.show = ""

    def reset(self) -> None:
        self.show = ""

    def snapshot(self) -> dict:
        return {"show": self.show}

    def restore(self, state: dict) -> None:
        self.show = state.get("show", "")

    def choices(self) -> list[tuple[str, str]]:
        return [("", "All apps"), ("here", f"On {self.world.this_device.name}"), ("away", "On other devices")]

    def pick(self, key: str) -> None:
        self.show = key
        self.shell.refresh_body()

    def line(self) -> str:
        return {"": "Everything you have, on every device", "here": f"What's on {self.world.this_device.name}",
                "away": "What you have on your other devices, ready to install here"}[self.show]

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-library")
        page.append(intro("Library", self.line()))
        return page

    def bar_items(self) -> list:
        items = [choice_button(lambda: dict(self.choices())[self.show], "layers", "Show", self.choices,
                               lambda: self.show, self.pick, name="dq-libmenu")]
        count = self.world.update_count()
        if count:
            items.append(BarWidget(Button("Update all", variant=Button.KEY, icon="refresh-cw",
                                          tooltip=f"{count} waiting across your devices",
                                          on_click=self.shell.update_all_apps if hasattr(self.shell, "update_all_apps") else None)))
        return items
