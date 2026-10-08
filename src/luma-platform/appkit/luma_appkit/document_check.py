# SPDX-License-Identifier: Apache-2.0
"""Checklist markers for document editors, also usable in TextView snapshots.

Editors retain selection, hit testing and document persistence. This component
owns the shared well/outline and checked appearance.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk
from . import media_style as style, lumaui
from .lumaui_tokens import DOCUMENT_CHECK as D


def snapshot_document_check(snapshot: Gtk.Snapshot, owner: Gtk.Widget, x: float,
                            y: float, *, checked: bool = False,
                            appearance: str = "well", hue: float | None = None) -> None:
    """Draw a document check at editor coordinates using the owner's palette."""
    if appearance not in ("well", "outline"):
        raise ValueError("document check appearance must be well or outline")
    size = D["size"] if appearance == "well" else D["outline_size"]
    radius = D["radius"] if appearance == "well" else D["outline_radius"]
    bounds = style.rect(x, y, size, size)
    shape = style.rounded(bounds, radius)
    snapshot.push_rounded_clip(shape)
    if checked:
        colour = style.colour(owner, "luma_well_on")
        if hue is not None:
            colour = Gdk.RGBA()
            colour.parse(lumaui.oklch_rgba(D["checked_lightness_ratio"], D["checked_chroma_ratio"], hue))
        snapshot.append_color(colour, bounds)
        snapshot.append_inset_shadow(shape, style.colour(owner, "luma_well_on_ink", D["checked_lip_alpha"]), 0, 1, 0, 0)
    elif appearance == "well":
        snapshot.append_color(style.colour(owner, "luma_well"), bounds)
        snapshot.append_inset_shadow(shape, style.colour(owner, "luma_well_shade"), 0, 1, 0, 2)
    snapshot.pop()
    if not checked:
        width = 1 if appearance == "well" else D["outline_width"]
        snapshot.append_border(shape, [width] * 4,
                               [style.colour(owner, "luma_well_ring")] * 4)
        return
    glyph = D["glyph"]
    ink = style.colour(owner, "luma_well_on_ink")
    cr = snapshot.append_cairo(bounds)
    cr.set_source_rgba(ink.red, ink.green, ink.blue, ink.alpha)
    cr.translate(x + (size - glyph) / 2, y + (size - glyph) / 2)
    cr.scale(glyph / 24, glyph / 24)
    cr.set_line_width(3)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    cr.move_to(20, 6)
    cr.line_to(9, 17)
    cr.line_to(4, 12)
    cr.stroke()


class DocumentCheck(Gtk.Widget):
    """A noninteractive document marker; the owning editor handles activation."""
    __gtype_name__ = "LumaUIDocumentCheck"

    def __init__(self, *, checked: bool = False, appearance: str = "well",
                 hue: float | None = None) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        if appearance not in ("well", "outline"):
            raise ValueError("document check appearance must be well or outline")
        self.checked = bool(checked)
        self.appearance = appearance
        self.hue = hue
        self.add_css_class("lumaui-document-check")
        size = D["size"] if appearance == "well" else D["outline_size"]
        self.set_size_request(size, size)

    def set_checked(self, checked: bool) -> None:
        self.checked = bool(checked)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        snapshot_document_check(snapshot, self, 0, 0, checked=self.checked,
                                appearance=self.appearance, hue=self.hue)
