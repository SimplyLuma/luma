# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: the glow behind a lit header, from hues or the Luma tone (ID2).

`ContentLitHeader(hues=(h, h2, h3))` lights an island from up to three hues
(an album's typographic cover, an app's own colour: v70 `.dapp` puts the
first at the top left and the second at the bottom right); and
`ContentLitHeader(tone="luma")` is the system update's own wash (v70
`.dsysbg`: orange from the top left, pink from the bottom right, a peach
light at the top). This module draws that glow; the header owns its place,
height and the photo light. Colours come from the `lit_glow` tokens: the hue
glow's lightness, chroma and alpha per light or dark, the Luma glow's three
colours. It fades out downwards like the photo light.

Private to the rows family; apps use ContentLitHeader.
"""
from __future__ import annotations

from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from . import lumaui, rows_tokens  # noqa: E402

__all__: list[str] = []

#: Where each hue lights from, as (x, y, radius x, radius y) of the header: v70 .dapp and .dsysbg.
_PLACES = ((0.0, 0.0, 0.9, 1.4), (1.0, 1.0, 0.6, 1.0), (0.6, 0.0, 0.5, 0.8))


def _stops(colour: Gdk.RGBA, until: float) -> list[Gsk.ColorStop]:
    clear = colour.copy()
    clear.alpha = 0.0
    stops = []
    for offset, value in ((0.0, colour), (until, clear)):
        stop = Gsk.ColorStop()
        stop.offset, stop.color = offset, value
        stops.append(stop)
    return stops


def glow_colours(hues: Sequence[float] | None, luma: bool, dark: bool) -> list[Gdk.RGBA]:
    """The glow's colours, first to third: from the hues, or the Luma tone's."""
    tokens = rows_tokens.group("lit_glow")
    out = []
    if luma:
        for text in tokens["luma"]:
            colour = Gdk.RGBA()
            colour.parse(text)
            out.append(colour)
        return out
    m = tokens["dark" if dark else "light"]
    for index, hue in enumerate(list(hues or ())[:3]):
        colour = Gdk.RGBA()
        # The second and third hues are quieter (v70 .dapp: .95 then .45).
        alpha = m["peak_alpha"] * (1.0 if index == 0 else m["second_alpha"])
        colour.parse(lumaui.oklch_rgba(m["lightness_ratio"], m["chroma_ratio"], hue, alpha))
        out.append(colour)
    return out


class HueGlow(Gtk.Widget):
    """Radial light from up to three colours, fading out towards the header's foot."""

    __gtype_name__ = "LumaUIHueGlow"

    def __init__(self) -> None:
        super().__init__(can_target=False, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.add_css_class("lumaui-lit-glow")
        self.hues: tuple[float, ...] = ()
        self.luma = False

    def set_source(self, hues: Sequence[float] | None, luma: bool) -> None:
        self.hues = tuple(float(h) % 360 for h in (hues or ()))[:3]
        self.luma = bool(luma)
        self.set_visible(bool(self.hues) or self.luma)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        from .lumaui import _desktop_dark
        colours = glow_colours(self.hues, self.luma, _desktop_dark())
        bounds = Graphene.Rect().init(0, 0, width, height)
        fade = rows_tokens.group("lit_glow")
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        stops = []
        for offset, alpha in ((fade["mask_start_pct"] / 100, 1.0), (fade["mask_end_pct"] / 100, 0.0)):
            colour = Gdk.RGBA()
            colour.red = colour.green = colour.blue = 0.0
            colour.alpha = alpha
            stop = Gsk.ColorStop()
            stop.offset, stop.color = offset, colour
            stops.append(stop)
        snapshot.append_linear_gradient(bounds, Graphene.Point().init(0, 0), Graphene.Point().init(0, height), stops)
        snapshot.pop()
        for colour, (x, y, rx, ry) in zip(colours, _PLACES):
            center = Graphene.Point().init(width * x, height * y)
            snapshot.append_radial_gradient(bounds, center, max(1.0, width * rx), max(1.0, height * ry), 0.0, 1.0,
                                            _stops(colour, 0.72))
        snapshot.pop()
