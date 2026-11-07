# SPDX-License-Identifier: Apache-2.0
"""Pictures Depot composes for Looks and Tweaks from their own data (handoff 9.3, 9.4): nobody makes
marketing art by hand.

- A wallpaper Look: on the desktop with a window, on the bare desktop with the dock, and on a phone's
  lock screen at 9:41.
- Any other Look: its specimen scaled up on a blurred wallpaper.
- A Tweak: Off → On, then the tweak in place (a desktop, or a phone for tweaks that don't run on one).
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import FramedPicture, icons

from ..assets import wallpaper
from ..model.world import Item, World
from .frames import Picture, dock_for, room_for, shot_file
from .look_pictures import specimen

__all__ = ["composed_pictures", "lock_screen", "off_on", "banner"]

LOCK_DATE = "Saturday, November 7"


def _art(world: World, name: str) -> str | None:
    return wallpaper(name, world.assets / "art" if world.assets is not None else None)


def lock_screen(day: str | None = None) -> Gtk.Widget:
    """9:41 and the date over the wallpaper, at the reference phone's own points."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
    box.add_css_class("dq-plock")
    time = Gtk.Label(label="9:41")
    time.add_css_class("time")
    when = Gtk.Label(label=day or LOCK_DATE)
    when.add_css_class("date")
    box.append(time)
    box.append(when)
    return box


def banner(glyph: str, name: str, text: str) -> Gtk.Widget:
    """The frosted chip near a frame's foot: the tweak's glyph, its name and what it does."""
    chip = Gtk.Grid(column_spacing=8, halign=Gtk.Align.CENTER)
    chip.add_css_class("dq-fchip")
    mark = icons.image(glyph)
    chip.attach(mark, 0, 0, 1, 2)
    title = Gtk.Label(label=name, xalign=0)
    title.add_css_class("title")
    words = Gtk.Label(label=text, xalign=0)
    words.add_css_class("text")
    chip.attach(title, 1, 0, 1, 1)
    chip.attach(words, 1, 1, 1, 1)
    return chip


def off_on(glyph: str, off: str, on: str) -> Gtk.Widget:
    """Two frosted panels, OFF and ON, an arrow between them; the On one ringed in the accent."""
    row = Gtk.Box(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL, hexpand=True, vexpand=True)
    row.add_css_class("dq-ba")
    for index, (word, text) in enumerate((("Off", off), ("On", on))):
        if index:
            arrow = icons.image("arrow-right")
            arrow.add_css_class("arrow")
            arrow.set_valign(Gtk.Align.CENTER)
            row.append(arrow)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        panel.add_css_class("side")
        if index:
            panel.add_css_class("on")
        kicker = Gtk.Label(label=word.upper())
        kicker.add_css_class("kicker")
        well = Gtk.Box(halign=Gtk.Align.CENTER)
        well.add_css_class("glyph")
        mark = icons.image(glyph)
        mark.set_hexpand(True)
        well.append(mark)
        words = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=1)
        words.add_css_class("text")
        for widget in (kicker, well, words):
            panel.append(widget)
        row.append(panel)
    return row


def composed_pictures(world: World, item: Item) -> list[Picture]:
    prism = _art(world, "prism")
    if item.kind == "look" and item.look is not None:
        look = item.look
        if look.art:
            art = _art(world, look.art)
            icons_, running = dock_for(world, world.item("app", "tide"))
            tide = shot_file(world, "tide-desktop-dark")
            return [
                Picture("desk", f"{look.name} behind a window",
                        lambda r: FramedPicture.desktop(tide, backdrop=art, dock=icons_, running=running, radius=r)),
                Picture("bare", f"{look.name} on the desktop",
                        lambda r: FramedPicture.desktop(None, backdrop=art, dock=icons_, radius=r)),
                Picture("lock", f"{look.name} on a phone's lock screen",
                        lambda r: FramedPicture.phone(art, backdrop=art, screen=lock_screen(), radius=r), phone=True),
            ]
        # The tile's specimen, scaled up: a theme's window fills about three quarters of its desktop.
        width = 300 if look.kind == "Icons" else 270
        return [Picture("specimen", f"{look.name}",
                        lambda r: FramedPicture.specimen_of(specimen(look), backdrop=prism, radius=r,
                                                            specimen_width=width))]
    tweak = item.tweak
    if tweak is None:
        return []
    on_text = tweak.on_text or tweak.line
    pictures = [Picture("offon", f"{tweak.name}, off and on",
                        lambda r: FramedPicture.specimen_of(off_on(tweak.icon, tweak.off or "As Luma comes", on_text),
                                                            backdrop=prism, radius=r, specimen_width=389))]
    chip = lambda: banner(tweak.icon, tweak.name, on_text)  # noqa: E731
    if "desktop" in item.runs:
        icons_, _running = dock_for(world, world.item("app", "filer"))
        filer = shot_file(world, "filer-desktop-dark")
        pictures.append(Picture("inplace", f"{tweak.name} on the desktop",
                                lambda r: FramedPicture.desktop(filer, backdrop=prism, dock=icons_, radius=r,
                                                                banner=chip())))
    else:
        tide = shot_file(world, "tide-phone-dark")
        room = room_for(item.id)
        pictures.append(Picture("inplace", f"{tweak.name} on a phone",
                                lambda r: FramedPicture.phone(tide, backdrop=room, radius=r, banner=chip()),
                                phone=True))
    return pictures
