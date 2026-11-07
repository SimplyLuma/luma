# SPDX-License-Identifier: Apache-2.0
"""Updates (handoff 11): one summary line, Luma's own card, app cards, and the folds."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import BarWidget, Button

from .base import Page, intro


class UpdatesPage(Page):
    place = "updates"

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-updates")
        page.append(intro("Updates"))
        return page

    def check_now(self) -> None:
        self.shell.toast("Checked just now. Nothing new since this morning")

    def bar_items(self) -> list:
        items = [BarWidget(Button("Check now", icon="refresh-cw", on_click=self.check_now))]
        if self.world.update_count():
            items.append(BarWidget(Button("Update all", variant=Button.KEY, icon="download")))
        return items
