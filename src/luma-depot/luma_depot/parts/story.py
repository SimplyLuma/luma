# SPDX-License-Identifier: Apache-2.0
"""Today's one editors' story (4.1): a blurred photo, a kicker, the headline, a sentence or two, the app
in an inset strip with a proper button, and the app's own screen rising from the bottom-right corner."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import Button

from ..model.world import Story, World
from ..pages.base import label
from .face import Backdrop, face, load_picture, paint_gradient

__all__ = ["story_card"]


class _Cap(Gtk.Widget):
    """Its child at most `cap` wide (CSS max-width): it asks for `cap` and takes what it is given."""

    __gtype_name__ = "DepotCap"

    def __init__(self, child: Gtk.Widget, cap: int) -> None:
        super().__init__(halign=Gtk.Align.START)
        self.cap = cap
        child.set_parent(self)
        self.child = child

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        low, natural, _b, _c = self.child.measure(orientation, min(for_size, self.cap) if for_size >= 0 and
                                                  orientation == Gtk.Orientation.VERTICAL else for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return low, max(low, self.cap), -1, -1
        return low, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self.child.allocate(min(width, self.cap), height, baseline, None)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None


def story_card(world: World, story: Story, *, art: str | None, shot: str | None, here: bool,
               on_open, on_action, compact: bool = False) -> Gtk.Widget:
    """v73 .dq-hero: 36 inside (40 at the foot), the words 46% of the inner width beside the app's rising screen
    (the whole width when compact), the blurred photo 40 past every edge (.dq-hbg inset -40)."""
    item = world.item("app", story.app)
    card = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
    card.add_css_class("dq-hero")
    paint_gradient(card, (0.36, 0.09, story.hue), (0.2, 0.05, story.hue + 40), 135)
    ground = Backdrop(art)
    ground.add_css_class("dq-hbg")
    card.add_overlay(ground)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL if compact else Gtk.Align.START,
                   valign=Gtk.Align.START)
    text.add_css_class("dq-htx")
    text.append(label(story.kick.upper(), "dq-hkick"))
    headline = label(story.title, "dq-htitle", wrap=True)
    text.append(_Cap(headline, 254 if compact else 338))  # max-width 13ch at 40/780 (30/780 compact)
    text.append(label(story.blurb, "dq-hblurb", wrap=True))
    strip = Gtk.Box()
    strip.add_css_class("dq-hfoot")
    strip.append(face(world, item, "sm"))
    who = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    who.append(label(item.name, "dq-hname", ellipsize=True))
    who.append(label(item.by, "dq-hby", ellipsize=True))
    strip.append(who)
    button = Button("Open" if here else "Install", variant=Button.KEY, icon=None if here else "download",
                    on_click=on_action)
    button.add_css_class("dq-hbtn")
    button.set_valign(Gtk.Align.CENTER)
    strip.append(button)
    text.append(_Cap(strip, 10_000 if compact else 360))  # max-width 360
    rising = None
    if shot and not compact:
        # The app's own screen rises from the bottom-right corner: 47% of the card wide, 26 from the right,
        # cropped by the card's foot (it overhangs by 84), tilted -3° (the CSS).
        rising = load_picture(shot)
        rising.set_content_fit(Gtk.ContentFit.COVER)
        rising.set_can_shrink(True)
        rising.add_css_class("dq-hshot")
        rising.set_can_target(False)
        card.add_overlay(rising)
    card.set_child(Gtk.Box())
    card.add_overlay(text)
    if compact:
        text.add_css_class("compact")  # the card's own 22 inside, as padding; the card is as tall as the words
        card.set_measure_overlay(text, True)
    inset = (36, 36, 40)  # sides, top, foot

    def place(overlay, widget, rect) -> bool:
        width, height = overlay.get_width(), overlay.get_height()
        if widget is ground:
            rect.x, rect.y, rect.width, rect.height = -40, -40, width + 80, height + 80
            return True
        if widget is rising:
            rect.width = round(width * 0.47)
            rect.height = round(rect.width * 698 / 1180)
            rect.x = width - 26 - rect.width
            rect.y = height + 84 - rect.height
            return True
        if widget is text and compact:
            rect.x, rect.y, rect.width = 0, 0, width
            rect.height = text.measure(Gtk.Orientation.VERTICAL, width)[1]
            return True
        if widget is text:
            side, top, foot = inset
            rect.width = round((width - 2 * side) * 0.46)
            rect.height = text.measure(Gtk.Orientation.VERTICAL, rect.width)[1]
            rect.x = side
            rect.y = top + max(0, (height - top - foot - rect.height) // 2)
            return True
        return False

    card.connect("get-child-position", place)
    if not compact:
        card.set_size_request(-1, 360)
    from .rows import _activatable
    return _activatable(card, on_open, story.title, story.blurb)
