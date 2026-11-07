# SPDX-License-Identifier: Apache-2.0
"""Search (handoff 12): the query in quotes, then results grouped Apps, Looks, Tweaks."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .base import Page, intro


class SearchPage(Page):
    place = "search"

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-search")
        page.append(intro(f"“{self.shell.query.strip()}”"))
        return page
