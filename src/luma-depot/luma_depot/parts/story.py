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


def story_card(world: World, story: Story, *, art: str | None, shot: str | None, here: bool,
               on_open, on_action, compact: bool = False) -> Gtk.Widget:
    item = world.item("app", story.app)
    card = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
    card.add_css_class("dq-hero")
    paint_gradient(card, (0.36, 0.09, story.hue), (0.2, 0.05, story.hue + 40), 135)
    ground = Backdrop(art)
    ground.add_css_class("dq-hbg")
    card.set_child(ground)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START, valign=Gtk.Align.START)
    text.add_css_class("dq-htx")
    text.append(label(story.kick.upper(), "dq-hkick"))
    from gi.repository import Adw
    headline = label(story.title, "dq-htitle", wrap=True)
    text.append(Adw.Clamp(maximum_size=340, tightening_threshold=340, child=headline, halign=Gtk.Align.START))
    blurb = label(story.blurb, "dq-hblurb", wrap=True)
    text.append(Adw.Clamp(maximum_size=437, tightening_threshold=437, child=blurb, halign=Gtk.Align.START))
    strip = Gtk.Box()
    strip.add_css_class("dq-hfoot")
    strip.append(face(world, item, "sm"))
    who = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    who.append(label(item.name, "dq-hname"))
    who.append(label(item.by, "dq-hby"))
    strip.append(who)
    button = Button("Open" if here else "Install", variant=Button.KEY, icon=None if here else "download",
                    on_click=on_action)
    button.add_css_class("dq-hbtn")
    strip.append(button)
    text.append(strip)
    if shot and not compact:
        # The app's own screen rises from the bottom-right corner: 47% of the card wide, 26 from the right,
        # cropped by the card's foot (it overhangs by 84), tilted -3° (the CSS).
        rising = load_picture(shot)
        rising.set_content_fit(Gtk.ContentFit.COVER)
        rising.set_can_shrink(True)
        rising.add_css_class("dq-hshot")
        rising.set_can_target(False)
        card.add_overlay(rising)

        def place(overlay, widget, rect) -> bool:
            if widget is not rising:
                return False
            width, height = overlay.get_width(), overlay.get_height()
            rect.width = round(width * 0.47)
            rect.height = round(rect.width * 698 / 1180)
            rect.x = width - 26 - rect.width
            rect.y = height + 84 - rect.height
            return True

        card.connect("get-child-position", place)
    card.add_overlay(text)
    card.set_measure_overlay(text, True)
    card.set_size_request(-1, 360)
    from .rows import _activatable
    return _activatable(card, on_open, f"{story.title}. {story.blurb}")
