# SPDX-License-Identifier: Apache-2.0

"""Maps' own photo surfaces: a cover photo, the Guides cards and a guide's header.

Everything around them (rows, the bar, its panels, buttons) is LumaUI's. What is
here is a picture with words over it, which no shared part draws.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Pango  # noqa: E402

from luma_appkit import PersonAvatar, apply_type  # noqa: E402
from luma_appkit import media_style  # noqa: E402

from .fixture import Guide, MapsFixture  # noqa: E402


def draw_cover(cr, width: int, height: int, pixels: GdkPixbuf.Pixbuf) -> None:
    """Paint `pixels` to cover the box, cropped at the centre (CSS `background: center/cover`)."""
    scale = max(width / pixels.get_width(), height / pixels.get_height())
    cr.save()
    cr.rectangle(0, 0, width, height)
    cr.clip()
    cr.translate((width - pixels.get_width() * scale) / 2, (height - pixels.get_height() * scale) / 2)
    cr.scale(scale, scale)
    Gdk.cairo_set_source_pixbuf(cr, pixels, 0, 0)
    cr.paint()
    cr.restore()


def cover_area(pixels: GdkPixbuf.Pixbuf, height: int) -> Gtk.DrawingArea:
    """A cover photo `height` tall that takes the width it is given."""
    area = Gtk.DrawingArea(hexpand=True, vexpand=True)
    area.set_content_height(height)
    area.set_draw_func(lambda _a, cr, width, tall: draw_cover(cr, width, tall, pixels))
    return area


def load_photo(fixture: MapsFixture, name: str) -> GdkPixbuf.Pixbuf:
    return GdkPixbuf.Pixbuf.new_from_file(str(fixture.asset_dir / name))


def _words_over_photo(guide: Guide, count: int, *, large: bool) -> Gtk.Widget:
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START, valign=Gtk.Align.END)
    words.add_css_class("mp-guide-words")
    title = apply_type(Gtk.Label(label=guide.title, xalign=0, wrap=True), "card-title" if large else "label",
                       weight=750 if large else 700)
    title.add_css_class("mp-guide-title")
    words.append(title)
    if large:
        line = Gtk.Box()
        line.add_css_class("mp-guide-by")
        line.append(PersonAvatar(guide.by if guide.by != "You" else "Nick", 18))
        line.append(apply_type(Gtk.Label(label=f"By {guide.by} · {count} places", xalign=0), "caption", weight=400))
        words.append(line)
    else:
        words.append(apply_type(Gtk.Label(label=f"{guide.by} · {count} places", xalign=0), "caption", weight=400))
    return words


class GuideCard(Gtk.Button):
    """A guide as a sideways card: its photo, its title, who made it and how many places."""

    __gtype_name__ = "LumaMapsGuideCard"

    def __init__(self, fixture: MapsFixture, guide: Guide, on_open: Callable[[Guide], None]) -> None:
        super().__init__()
        self.add_css_class("mp-guide-card")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.guide = guide
        overlay = Gtk.Overlay(child=cover_area(load_photo(fixture, guide.image), 104))
        shade = Gtk.Box(hexpand=True, vexpand=True)
        shade.add_css_class("mp-guide-shade")
        overlay.add_overlay(shade)
        overlay.add_overlay(_words_over_photo(guide, len(guide.place_ids), large=False))
        self.set_child(overlay)
        self.set_size_request(150, 104)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{guide.title}, {guide.by}, {len(guide.place_ids)} places"])
        self.connect("clicked", lambda _b: on_open(guide))

    def do_snapshot(self, snapshot) -> None:
        # A DrawingArea does not inherit its button's CSS corner clip.
        snapshot.push_rounded_clip(media_style.rounded(
            media_style.rect(0, 0, self.get_width(), self.get_height()), 16))
        Gtk.Button.do_snapshot(self, snapshot)
        snapshot.pop()


class GuideStrip(Gtk.ScrolledWindow):
    """The Guides: cards side by side, swiping sideways."""

    __gtype_name__ = "LumaMapsGuideStrip"

    def __init__(self, fixture: MapsFixture, on_open: Callable[[Guide], None]) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vscrollbar_policy=Gtk.PolicyType.NEVER,
                         propagate_natural_height=True)
        self.add_css_class("mp-guide-strip")
        row = Gtk.Box()
        for guide in fixture.guides:
            row.append(GuideCard(fixture, guide, on_open))
        self.set_child(row)


class GuideHeader(Gtk.Overlay):
    """A guide's photo with its title, who made it and the count."""

    __gtype_name__ = "LumaMapsGuideHeader"

    def __init__(self, fixture: MapsFixture, guide: Guide) -> None:
        super().__init__(child=cover_area(load_photo(fixture, guide.image), 132))
        self.add_css_class("mp-guide-header")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        shade = Gtk.Box(hexpand=True, vexpand=True)
        shade.add_css_class("mp-guide-shade")
        self.add_overlay(shade)
        self.add_overlay(_words_over_photo(guide, len(guide.place_ids), large=True))
        self.set_size_request(-1, 132)

    def do_snapshot(self, snapshot) -> None:
        snapshot.push_rounded_clip(media_style.rounded(
            media_style.rect(0, 0, self.get_width(), self.get_height()), 18))
        Gtk.Overlay.do_snapshot(self, snapshot)
        snapshot.pop()


def number_badge(number: int) -> Gtk.Label:
    """The red numeral before a place in a guide."""
    badge = apply_type(Gtk.Label(label=str(number), halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER),
                       "label", weight=700)
    badge.add_css_class("mp-guide-number")
    badge.set_size_request(28, 28)
    return badge
