# SPDX-License-Identifier: Apache-2.0
"""Rows and tiles the browse pages are built from (handoff 3.3, 5.1, 6.1, 7.1)."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Button, icons

from ..assets import data_file
from ..model import rules
from ..model.world import CategoryInfo, Item, World
from ..pages.base import label
from .face import face, load_picture

__all__ = ["action_button", "category_tile", "look_tile", "rank_row", "tweak_card"]


def _activatable(widget: Gtk.Widget, on_open: Callable[[], None], name: str, about: str = "") -> Gtk.Widget:
    """A tile, row or card: focusable, opens on click or Enter (16.4). It is named by its title; the rest
    of what it shows is its description."""
    widget.set_focusable(True)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [name])
    if about:
        widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [about])
    # A button inside (Install, Open) claims its own click, so this one only sees clicks elsewhere.
    click = Gtk.GestureClick()
    click.connect("released", lambda *_a: on_open())
    widget.add_controller(click)
    keys = Gtk.EventControllerKey()
    from gi.repository import Gdk
    keys.connect("key-pressed", lambda _c, keyval, *_a: (on_open(), True)[1]
                 if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) else False)
    widget.add_controller(keys)
    widget.set_cursor_from_name("pointer")
    return widget


class _Shares(Gtk.Widget):
    """Its children side by side, `gap` apart, each the same share of the room plus its own padding: CSS's
    `flex: 1` (a 0 basis), where Gtk.Box(homogeneous) would make the padded child's border box the equal one."""

    __gtype_name__ = "DepotShares"

    def __init__(self, children: list[Gtk.Widget], gap: int) -> None:
        super().__init__(hexpand=True)
        self.gap = gap
        self.kids = children
        for child in children:
            child.set_parent(self)

    @staticmethod
    def _pad(child: Gtk.Widget) -> int:
        if isinstance(child, Gtk.Label):
            return max(0, child.measure(Gtk.Orientation.HORIZONTAL, -1)[1] - child.get_layout().get_pixel_size()[0])
        return 0

    def _widths(self, width: int) -> list[int]:
        pads = [self._pad(c) for c in self.kids]
        share = max(0, (width - self.gap * (len(self.kids) - 1) - sum(pads)) / len(self.kids))
        return [max(round(share + pad), c.measure(Gtk.Orientation.HORIZONTAL, -1)[0]) for c, pad in zip(self.kids, pads)]

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            sizes = [c.measure(orientation, -1) for c in self.kids]
            extra = self.gap * (len(self.kids) - 1)
            return sum(m[0] for m in sizes) + extra, sum(m[1] for m in sizes) + extra, -1, -1
        widths = self._widths(for_size) if for_size >= 0 else [-1] * len(self.kids)
        sizes = [c.measure(orientation, w) for c, w in zip(self.kids, widths)]
        return max(m[0] for m in sizes), max(m[1] for m in sizes), -1, -1

    def do_size_allocate(self, width, height, baseline):
        gi.require_version("Gsk", "4.0")
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene, Gsk
        x = 0
        for child, w in zip(self.kids, self._widths(width)):
            child.allocate(w, height, -1, Gsk.Transform().translate(Graphene.Point().init(x, 0)))
            x += w + self.gap

    def do_dispose(self):
        for child in self.kids:
            child.unparent()
        self.kids = []


def action_button(world: World, item: Item, on_install: Callable[[], None], on_open: Callable[[], None]) -> Gtk.Widget:
    """A row's or card's button: Install (Fill) or Open / Installed / In use (Plain); never a pill."""
    key, words = rules.row_action(item, world)
    if key == "none":
        note = label(words, "dq-act-none", xalign=0.5)
        note.set_valign(Gtk.Align.CENTER)
        return note
    return Button(words, variant=Button.FILL if key == "install" else Button.PLAIN, size=Button.SM,
                  on_click=on_install if key == "install" else on_open)


def rank_row(world: World, item: Item, second: str, *, rank: int | None, on_open, on_install) -> Gtk.Widget:
    row = Gtk.Box()
    row.add_css_class("dq-rank")
    if rank is not None:
        row.append(label(str(rank), "dq-rn", xalign=0.5))
    row.append(face(world, item, "row"))
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    words.append(label(item.name, "dq-rt-name", ellipsize=True))
    words.append(label(second, "dq-rt-line", ellipsize=True))
    row.append(words)
    row.append(action_button(world, item, on_install, on_open))
    return _activatable(row, on_open, item.name, second)


def category_tile(world: World, category: CategoryInfo, *, on_open) -> Gtk.Widget:
    """A photo, a black foot, the name only; three of its most popular icons lifted by shadows (5.1)."""
    tile = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
    tile.add_css_class("dq-cat")
    photo = load_picture(data_file("photos", f"{category.photo}.webp") or "")
    photo.set_content_fit(Gtk.ContentFit.COVER)
    photo.set_can_shrink(True)
    photo.add_css_class("dq-catpic")
    if category.key == "media":
        photo.add_css_class("flip")  # mirrored so her face is clear of the icons
    tile.set_child(photo)
    foot = Gtk.Box(vexpand=True, hexpand=True)
    foot.add_css_class("dq-catfoot")
    tile.add_overlay(foot)
    name = label(category.name, "dq-catname")
    name.set_valign(Gtk.Align.END)
    name.set_halign(Gtk.Align.START)
    tile.add_overlay(name)
    apps = sorted((it for it in world.items("app") if it.category == category.key and not it.is_system
                   and world.can_run(it, world.here)),
                  key=lambda it: it.app.rating_count, reverse=True)[:3]
    faces = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START)
    faces.add_css_class("dq-catfaces")
    for it in apps:
        small = face(world, it, "xs")
        small.add_css_class("cat")
        small.set_size_request(-1, -1)  # 28 from the CSS: a size request counts the -6 overlap as width
        faces.append(small)
    tile.add_overlay(faces)
    tile.set_size_request(-1, 240)
    return _activatable(tile, on_open, category.name)


def look_tile(world: World, item: Item, *, on_open, in_use: bool) -> Gtk.Widget:
    """A 16:10 picture drawn from the Look's data, the name and "Kind · Maker"; no buttons (6.1)."""
    from ..pictures.look_pictures import specimen
    from luma_appkit import FramedPicture  # noqa: F401  (the same drawing as the item page)
    tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    tile.add_css_class("dq-lt2")
    frame = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
    frame.add_css_class("dq-ltp")
    look = item.look
    if look.art and world.assets is not None:
        picture = load_picture(str(world.assets / "art" / f"{look.art}.jpg"))
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_can_shrink(True)
        frame.set_child(picture)
    else:
        frame.set_child(specimen(look))
    if in_use:
        chip = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.END)
        chip.add_css_class("dq-ltuse")
        chip.append(icons.image("check"))
        chip.append(Gtk.Label(label="In use"))
        frame.add_overlay(chip)
    ratio = Gtk.AspectFrame(ratio=16 / 10, obey_child=False, xalign=0, yalign=0)
    ratio.set_child(frame)
    tile.append(ratio)
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    words.add_css_class("dq-ltn")
    words.append(label(look.name, "dq-ltn-name", ellipsize=True))
    words.append(label(f"{look.kind} · {look.by}", "dq-ltn-line", ellipsize=True))
    tile.append(words)
    return _activatable(tile, on_open, look.name, f"{look.kind} by {look.by}" + (", in use" if in_use else ""))


def tweak_card(world: World, item: Item, *, on_open, on_install) -> Gtk.Widget:
    """The glyph, name and "Kind · Maker", Install (or Installed), and exactly two lines (7.1)."""
    tweak = item.tweak
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class("dq-tc")
    head = Gtk.Box()
    head.add_css_class("dq-tc-head")
    head.append(face(world, item, "sm"))
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    words.append(label(tweak.name, "dq-tc-name", ellipsize=True))
    words.append(label(f"{tweak.kind} · {tweak.by}", "dq-tc-line", ellipsize=True))
    action = action_button(world, item, on_install, on_open)
    if isinstance(action, Gtk.Label):
        # v73 `.dq-tc header > span` is the words and the "Not for" note alike: flex 1 each, 12 apart.
        head.append(_Shares([words, action], 12))
    else:
        head.append(words)
        head.append(action)
    card.append(head)
    line = label(tweak.line, "dq-tc-text", wrap=True, lines=2)
    line.set_max_width_chars(1)
    line.set_yalign(0)
    card.append(line)
    return _activatable(card, on_open, tweak.name, tweak.line)
