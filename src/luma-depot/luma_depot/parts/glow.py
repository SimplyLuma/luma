# SPDX-License-Identifier: Apache-2.0
"""The item header's glow (8.2): a soft radial blur in the icon's own colour, ~720 wide at 28%,
faded on every side so it never meets an edge as a line."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

__all__ = ["Glow"]


class Glow(Gtk.Box):
    """A box that paints the glow behind its children; `colour` is "#rrggbb" (None: no glow)."""

    __gtype_name__ = "DepotGlow"

    def __init__(self, colour: str | None, *, centre_x: float = 290, centre_y: float | None = None,
                 width: float = 720, alpha: float = 0.28) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("dq-glow")
        self.colour = Gdk.RGBA()
        self.has_colour = bool(colour) and self.colour.parse(colour)
        self.centre = (centre_x, centre_y)
        self.width, self.alpha = width, alpha

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self.has_colour:
            self._glow(snapshot)
        Gtk.Box.do_snapshot(self, snapshot)

    def _glow(self, snapshot: Gtk.Snapshot) -> None:
        w, h = float(self.get_width()), float(self.get_height())
        cx, cy = self.centre[0], self.centre[1] if self.centre[1] is not None else 90.0
        # An ellipse ~720 wide that has faded to nothing before it meets any edge of the island.
        rx, ry = self.width / 2, self.width * 0.36
        stops = []
        for offset, alpha in ((0.0, self.alpha), (0.45, self.alpha * 0.55), (1.0, 0.0)):
            colour = self.colour.copy()
            colour.alpha = alpha
            stop = Gsk.ColorStop()
            stop.offset, stop.color = offset, colour
            stops.append(stop)
        snapshot.append_radial_gradient(Graphene.Rect().init(cx - rx, cy - ry, 2 * rx, 2 * ry), Graphene.Point().init(cx, cy),
                                        rx, ry, 0.0, 1.0, stops)
