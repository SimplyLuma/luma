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


class _Overhang(Gtk.Widget):
    """A tile's column: never asks for more than its track, and when a line is wider (Update ready in an 84 track)
    it runs past the right edge, its contents centred on the wider column, as the mockup's grid track grows."""

    __gtype_name__ = "DepotOverhang"

    def __init__(self) -> None:
        super().__init__()
        self.inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.inner.add_css_class("dq-tile-track")
        self.inner.set_parent(self)

    def append(self, child: Gtk.Widget) -> None:
        self.inner.append(child)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 0, -1, -1
        low, natural, _b, _c = self.inner.measure(orientation, -1)
        return low, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        # A grid track grows to its items' min-content: an ellipsizing name gives way, a state line does not.
        need = max(width, self.inner.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        self.inner.allocate(need, height, -1, None)

    def do_snapshot(self, snapshot):
        self.snapshot_child(self.inner, snapshot)

    def do_dispose(self):
        if self.inner is not None:
            self.inner.unparent()
            self.inner = None


class _Spill(Gtk.Widget):
    """A tile's name (v73 .dq-tface b, max-width 104): it never widens its tile; when the tile is narrower it runs
    past both edges, centred, and only beyond 104 does it ellipsize."""

    __gtype_name__ = "DepotSpill"
    CAP = 104

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__()
        child.set_parent(self)
        self.child = child

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, min(self.child.measure(orientation, -1)[1], self.CAP), -1, -1
        low, natural, _b, _c = self.child.measure(orientation, -1)
        return low, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        gi.require_version("Gsk", "4.0")
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene, Gsk
        need = max(width, min(self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[1], self.CAP))
        self.child.allocate(need, height, -1, Gsk.Transform().translate(Graphene.Point().init((width - need) / 2, 0)))

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None


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
        # .dq-tiles: as many 112-wide columns as fit, 10 apart, rows 16 apart (84 and 4 by 8 when compact)
        compact = shell.is_compact()
        grid = Grid(lambda w: max(1, (w + 4) // 88) if compact else max(1, (w + 10) // 122),
                    column_gap=4 if compact else 10, row_gap=8 if compact else 16, css="dq-tiles")
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
        tile = _Overhang()
        tile.add_css_class("dq-tile")
        if not here:
            tile.add_css_class("away")
        open_button = Gtk.Button(halign=Gtk.Align.CENTER)
        open_button.add_css_class("dq-tface")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        icon = face(world, item, "mdc" if shell.is_compact() else "md")  # .dq-compact .dq-face.md: 56, 13 round
        icon.set_halign(Gtk.Align.CENTER)
        inner.append(icon)
        inner.append(_Spill(label(item.name, "dq-tile-name", xalign=0.5, ellipsize=True)))
        open_button.set_child(inner)
        open_button.update_property([Gtk.AccessibleProperty.LABEL], [item.name])
        open_button.connect("clicked", lambda _b: opener(shell, "app", item.id)())
        tile.append(open_button)
        if here and pending is not None and me in pending.devices:
            state = Gtk.Box(halign=Gtk.Align.CENTER)
            state.add_css_class("dq-tile-state")
            state.add_css_class("up")
            mark = icons.image("refresh-cw")
            mark.set_valign(Gtk.Align.START)
            state.append(mark)
            state.append(Gtk.Label(label="Update ready", valign=Gtk.Align.START))
            tile.append(state)
        elif here:
            installed = label("Installed", ("dq-tile-state",), xalign=0.5)
            installed.set_yalign(0)  # the words at the top of the 22 row, as the mockup's line sits
            tile.append(installed)
        else:
            # .dq-tget: a quiet 22 px pill, the glyph and the word (not a row button)
            get = Gtk.Button(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            get.add_css_class("dq-tget")
            words = Gtk.Box()
            words.append(icons.image("download"))
            words.append(Gtk.Label(label="Install"))
            get.set_child(words)
            get.connect("clicked", lambda _b: install_here(shell, "app", item.id)())
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
            waiting = f"{count} waiting across your devices"
            update = Button("Update" if self.shell.is_compact() else "Update all", variant=Button.KEY,
                            icon="refresh-cw", tooltip=waiting,
                            on_click=lambda: self.shell.store.update_all(self.shell.done))
            update.update_property([Gtk.AccessibleProperty.LABEL], [waiting])  # the mockup's title names it
            items.append(BarWidget(update))
        return items
