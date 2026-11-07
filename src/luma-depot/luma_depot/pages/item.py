# SPDX-License-Identifier: Apache-2.0
"""The item page (handoff 8): one layout for an app, a Look and a Tweak."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import BarWidget, Button, IconOnlyButton

from ..model import rules
from .base import Page, intro


class ItemPage(Page):
    place = "item"
    searchable = False

    @property
    def item(self):
        kind, item_id = self.shell.item
        return self.world.item(kind, item_id)

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-item")
        item = self.item
        page.append(intro(item.name, item.line))
        return page

    def bar_items(self) -> list:
        items = []
        for button in rules.bar_for(self.item, self.world).buttons:
            if button.variant == "quiet":
                label = Gtk.Label(label=button.label)
                label.add_css_class("dq-bar-none")
                items.append(BarWidget(label))
            elif not button.label:
                items.append(BarWidget(IconOnlyButton(button.icon, button.tooltip)))
            else:
                items.append(BarWidget(Button(button.label, variant=Button.KEY if button.variant == "key" else Button.FILL,
                                              icon=button.icon, tooltip=button.tooltip or None)))
        return items
