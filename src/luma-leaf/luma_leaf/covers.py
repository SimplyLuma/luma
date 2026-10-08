# SPDX-License-Identifier: Apache-2.0
"""Book covers use LumaUI CoverArt; v70 sample editions are fixture pictures."""
from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Graphene, Gsk, Gtk

from luma_appkit import CoverArt


def book_cover(*, title: str, author: str, image: str | None, width: int,
               edition: dict | None = None, flexible: bool = False) -> Gtk.Widget:
    """Use real art first, then sample edition art, then the kit's generated art."""
    picture = image or (edition.get("art") if edition else None)
    if edition is not None and width <= 34 and picture:
        # The contents drawer uses the sample edition at 34 px; CoverArt's
        # shared mini size is 46 px for the shelf's Also reading rows.
        miniature_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(picture, 34, 51, False)
        miniature = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(miniature_pixbuf))
        miniature.set_content_fit(Gtk.ContentFit.FILL)
        miniature.set_size_request(34, 51)
        return miniature
    size = "mini" if width <= 46 else "hero" if width >= 210 else "tile"
    # TODO(kit-request leaf-08-cover-inventory): let conform see artwork text through CoverArt.
    # TODO(kit-request leaf-11-book-cover-perspective): featured book presentation belongs to CoverArt.
    cover = CoverArt(title, author, picture=picture, shape="book", size=size, flexible=flexible)
    if size == "tile" and not flexible:
        cover.set_size_request(width, round(width * 1.5))
    return cover


class AvailabilityGlyph(Gtk.Widget):
    """A disc here, a ring on a reachable server, a slashed ring when offline."""

    __gtype_name__ = "LeafAvailabilityGlyph"
    LABELS = {"here": "On this device", "stream": "On a server you can reach", "gone": "Out of reach"}

    def __init__(self, state: str = "here", size: int = 15) -> None:
        super().__init__()
        self.state = state
        self.size = size
        self.add_css_class("luma-avail")
        self.set_valign(Gtk.Align.CENTER)
        self.set_tooltip_text(self.LABELS.get(state, ""))

    def set_state(self, state: str) -> None:
        self.state = state
        self.set_tooltip_text(self.LABELS.get(state, ""))
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        colour = self.get_color()
        scale = self.size / 24
        builder = Gsk.PathBuilder.new()
        builder.add_circle(Graphene.Point().init(12 * scale, 12 * scale), 6.2 * scale)
        circle = builder.to_path()
        if self.state == "here":
            snapshot.append_fill(circle, Gsk.FillRule.WINDING, colour)
            return
        stroke = Gsk.Stroke.new(1.7 * scale)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(circle, stroke, colour)
        if self.state == "gone":
            slash = Gsk.PathBuilder.new()
            slash.move_to(6.5 * scale, 17.5 * scale)
            slash.line_to(17.5 * scale, 6.5 * scale)
            snapshot.append_stroke(slash.to_path(), stroke, colour)
