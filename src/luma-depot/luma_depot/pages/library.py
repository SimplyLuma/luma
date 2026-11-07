# SPDX-License-Identifier: Apache-2.0
"""Library (handoff 10): an icon grid of everything you have, one line of state each."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import BarWidget, Button, Tooltip, icons

from ..parts.face import face
from ..parts.grid import Grid
from .base import Page, intro, label
from .common import empty, install_here, opener
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
        shell, world = self.shell, self.world
        me = world.here
        mine = sorted((it for it in world.items("app") if not it.is_system and world.on_devices(it)
                       and (it.seal != "luma" or it.id in world.kept)), key=lambda it: it.name.casefold())
        shown = [it for it in mine if not self.show or (self.show == "here") == (me in world.on_devices(it))]
        grid = Grid(lambda w: 3 if shell.is_compact() or w < 640 else max(4, min(8, (w + 8) // 124)),
                    column_gap=8, row_gap=22, css="dq-tiles")
        for item in shown:
            grid.append(self._tile(item))
        page.append(grid if shown else empty())
        return page

    def _tile(self, item) -> Gtk.Widget:
        """The icon, the name and one line of state: Installed, Update ready, or Install (10)."""
        shell, world = self.shell, self.world
        me = world.here
        on = world.on_devices(item)
        here = me in on
        pending = world.pending_for(item.id)
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        tile.add_css_class("dq-tile")
        if not here:
            tile.add_css_class("away")
        open_button = Gtk.Button()
        open_button.add_css_class("dq-tface")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        icon = face(world, item, "md")
        icon.set_halign(Gtk.Align.CENTER)
        if not here:
            icon.set_opacity(0.55)  # only on other devices: dimmed
        inner.append(icon)
        inner.append(label(item.name, "dq-tile-name", xalign=0.5, ellipsize=True))
        open_button.set_child(inner)
        open_button.update_property([Gtk.AccessibleProperty.LABEL], [item.name])
        open_button.connect("clicked", lambda _b: opener(shell, "app", item.id)())
        tile.append(open_button)
        if here and pending is not None and me in pending.devices:
            state = Gtk.Box(halign=Gtk.Align.CENTER)
            state.add_css_class("dq-tile-state")
            state.add_css_class("up")
            state.append(icons.image("refresh-cw"))
            state.append(Gtk.Label(label="Update ready"))
            tile.append(state)
        elif here:
            tile.append(label("Installed", ("dq-tile-state",), xalign=0.5))
        elif not world.can_run(item, me):
            tile.append(label(f"No {world.this_device.form_name.lower()} version", ("dq-tile-state",), xalign=0.5))
        else:
            get = Button("Install", variant=Button.PLAIN, size=Button.SM, icon="download",
                         on_click=install_here(shell, "app", item.id))
            get.add_css_class("dq-tget")
            get.set_halign(Gtk.Align.CENTER)
            get.update_property([Gtk.AccessibleProperty.LABEL], [f"Install {item.name} here"])
            others = [world.device(d).name for d in on if d != me]
            if others:
                Tooltip.attach(get, "On " + ", ".join(others), label=False)
            tile.append(get)
        return tile

    def bar_items(self) -> list:
        items = [choice_button(lambda: dict(self.choices())[self.show], "layers", "Show", self.choices,
                               lambda: self.show, self.pick, name="dq-libmenu")]
        count = self.world.update_count()
        if count:
            items.append(BarWidget(Button("Update all", variant=Button.KEY, icon="refresh-cw",
                                          tooltip=f"{count} waiting across your devices",
                                          on_click=self.shell.update_all_apps if hasattr(self.shell, "update_all_apps") else None)))
        return items
