# SPDX-License-Identifier: Apache-2.0
"""A photo collection cover with shared image loading, caption and lower scrim."""
from __future__ import annotations

import math
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Graphene, Gsk, Gtk

from . import lumaui_tokens as tokens
from . import media_style as style
from .media_grid import MediaTile
from .content_type import TypeLabel

C = tokens.MEDIA["collection"]


class _CollectionPicture(MediaTile):
    """Reuse the media tile's bounded asynchronous decoder and cover renderer."""

    def __init__(self, picture, aspect):
        self.aspect = aspect
        super().__init__(picture)
        self.set_can_target(False)
        self.set_focusable(False)

    def _picture_height(self, width):
        return width / self.aspect

    def do_snapshot(self, snapshot):
        super().do_snapshot(snapshot)
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        stops = []
        for offset, alpha in ((0, 0), (1, C["shade_alpha"])):
            stop = Gsk.ColorStop()
            stop.offset = offset
            stop.color = style.colour(self, "luma_media_shadow", alpha)
            stops.append(stop)
        start, end = Graphene.Point(), Graphene.Point()
        start.init(0, height * C["shade_start"])
        end.init(0, height)
        snapshot.append_linear_gradient(style.rect(0, 0, width, height), start, end, stops)


class MediaCollectionCard(Gtk.Button):
    """An activatable collection cover; picture accepts a paintable, path or Gio.File.

    The title and detail label the button. `aspect` is width / height; a cover
    fills the available width and decodes again when a larger preview is needed.
    """

    __gtype_name__ = "LumaUIMediaCollectionCard"

    def __init__(self, picture=None, *, title="", detail="", aspect=1.6, on_activate=None):
        if not math.isfinite(aspect) or aspect <= 0:
            raise ValueError("a collection cover aspect must be finite and positive")
        super().__init__(hexpand=True, halign=Gtk.Align.FILL, overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("lumaui-media-collection")
        self.set_size_request(C["min_width"], -1)
        self.picture = _CollectionPicture(picture, aspect)
        motion = Gtk.EventControllerMotion()
        hover_target = (C["hover_scale"] - 1) / (tokens.MEDIA["tile"]["hover_scale"] - 1)
        motion.connect("enter", lambda *_: self.picture._hover_to(hover_target))
        motion.connect("leave", lambda *_: self.picture._hover_to(0))
        self.add_controller(motion)
        overlay = Gtk.Overlay(child=self.picture)
        self.title = TypeLabel(title, role="media_collection_title")
        self.detail = TypeLabel(detail, role="media_collection_detail")
        self.title.set_halign(Gtk.Align.START)
        self.title.set_valign(Gtk.Align.END)
        self.title.set_margin_start(C["title_left"])
        self.title.set_margin_bottom(C["title_bottom"])
        self.detail.set_halign(Gtk.Align.END)
        self.detail.set_valign(Gtk.Align.END)
        self.detail.set_margin_end(C["detail_right"])
        self.detail.set_margin_bottom(C["detail_bottom"])
        self.detail.set_opacity(.82)
        for label in (self.title, self.detail):
            label.set_can_target(False)
            overlay.add_overlay(label)
        self.set_child(overlay)
        self.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(x for x in (title, detail) if x)])
        if on_activate is not None:
            self.connect("clicked", lambda _button: on_activate())

    def set_picture(self, picture):
        self.picture.set_picture(picture)
