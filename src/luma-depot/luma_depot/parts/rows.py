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


def _activatable(widget: Gtk.Widget, on_open: Callable[[], None], name: str) -> Gtk.Widget:
    """A tile, row or card: focusable, opens on click or Enter (16.4)."""
    widget.set_focusable(True)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [name])
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


def action_button(world: World, item: Item, on_install: Callable[[], None], on_open: Callable[[], None]) -> Gtk.Widget:
    """A row's or card's button: Install (Fill) or Open / Installed / In use (Plain); never a pill."""
    key, words = rules.row_action(item, world)
    if key == "none":
        return label(words, "dq-act-none")
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
    return _activatable(row, on_open, f"{item.name}, {second}")


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
    apps = sorted((it for it in world.items("app") if it.category == category.key and not it.is_system),
                  key=lambda it: it.app.rating_count, reverse=True)[:3]
    faces = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START)
    faces.add_css_class("dq-catfaces")
    for it in apps:
        small = face(world, it, "xs")
        small.add_css_class("cat")
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
    return _activatable(tile, on_open, f"{look.name}, {look.kind} by {look.by}" + (", in use" if in_use else ""))


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
    head.append(words)
    head.append(action_button(world, item, on_install, on_open))
    card.append(head)
    line = label(tweak.line, "dq-tc-text", wrap=True, lines=2)
    line.set_max_width_chars(1)
    card.append(line)
    return _activatable(card, on_open, f"{tweak.name}, {tweak.line}")
