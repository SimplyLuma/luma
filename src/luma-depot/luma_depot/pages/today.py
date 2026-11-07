# SPDX-License-Identifier: Apache-2.0
"""Today (handoff 4): the one editors' story, Popular beside New and updated, then Looks and Tweaks."""
from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .base import Page, kicker_title


class TodayPage(Page):
    place = "today"

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-today")
        page.append(kicker_title(date.today().strftime("%A, %B %-d"), "Today"))
        return page
