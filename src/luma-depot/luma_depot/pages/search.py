# SPDX-License-Identifier: Apache-2.0
"""Search (handoff 12): the query in quotes, then results grouped Apps, Looks, Tweaks."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Inset, icons

from ..parts.rows import rank_row
from .base import Page, intro, label, section
from .common import install_here, opener


class SearchPage(Page):
    place = "search"

    def build(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-search")
        query = self.shell.query.strip()
        page.append(intro(f"\u201c{query}\u201d"))
        shell, world = self.shell, self.world
        q = query.casefold()

        def hit(it) -> bool:
            return q in f"{it.name} {it.line} {it.by}".casefold()

        switch = next(((name, ids) for name, ids in world.switching
                       if name.casefold() in q or q in name.casefold()), None)
        apps = [it for it in world.items("app")
                if hit(it) or any(q in name.casefold() for name in (it.app.alternatives or ()))]
        looks = [it for it in world.items("look") if hit(it)]
        tweaks = [it for it in world.items("tweak") if hit(it)]

        def rows(items) -> Gtk.Widget:
            inset = Inset()
            for it in items[:8]:
                inset.append(rank_row(world, it, it.line, rank=None, on_open=opener(shell, it.kind, it.id),
                                      on_install=install_here(shell, it.kind, it.id)))
            return inset

        if switch is not None:
            name, ids = switch
            note = Gtk.Box()
            note.add_css_class("dq-swhit")
            note.append(icons.image("arrow-right"))
            note.append(label(f"{name} isn't on Luma. These do the same job:", "dq-swhit-text", wrap=True))
            page.append(note)
            page.append(rows([world.item("app", i) for i in ids if world.item("app", i) is not None]))
            apps = [it for it in apps if it.id not in ids]
        for title, items in ((("More apps" if switch else "Apps"), apps), ("Looks", looks), ("Tweaks", tweaks)):
            if items:
                page.append(section(title, rows(items)))
        if switch is None and not (apps or looks or tweaks):
            nothing = Gtk.Box()
            nothing.add_css_class("dq-empty-line")
            nothing.append(label("Nothing called that yet.", "dq-empty"))
            make = Gtk.Button(label="Make it for Luma")
            make.add_css_class("dq-link")
            make.connect("clicked", lambda _b: Gtk.UriLauncher.new("https://simplyluma.com/depot/publish")
                         .launch(shell, None, None, None))
            nothing.append(make)
            page.append(nothing)
        return page
