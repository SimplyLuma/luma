# SPDX-License-Identifier: Apache-2.0
"""LumaUI media: CoverArt (backlog MD5), an album's or a book's cover.

Real art always wins. Without it the kit draws a deterministic typographic
cover from the title, so the same album looks the same everywhere, in every
app, on every machine.

    CoverArt("Blue Hour", "Mara Sol", picture=texture, size="hero")         # Tide, real art
    CoverArt("Blue Hour", "Mara Sol", hues=(250, 285, 210), size="tile")     # Tide, v70's own hues
    CoverArt("The Salt Road", "I. Varga", shape="book", size="mini")         # Leaf, generated

- `shape="album"` — v70 `.cv`: square, a soft light from the top left over
  a diagonal from the album's hue to its deep hue; the title in the reading
  serif, italic, 9% in and 17% up, the artist in small caps under it. `mini`
  (a sidebar or list) shows the colour only.
- `shape="book"` — v70 `.lfcov`: 2:3, the spine round 2 and the fore-edge 5,
  a spine shade and sheen, the title and author centred in the book serif,
  and the page edges behind it. Edition artwork is the app's picture.
- `size` — `hero` (264 album, 210 book), `tile` (fills the width it is
  given; 160 or 140 natural) or `mini` (26 album, 46 book).
- `hues` — (h, h2, h3) in degrees; from the title when not given.

Pictures that are paths decode off the main loop (the media family's shared
loader). The cover reads as "<title>, <subtitle>" and is an image to
assistive technology.
"""
from __future__ import annotations

import math
from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402
from . import media_style as style  # noqa: E402

__all__ = ["CoverArt", "COVER_SHAPES", "COVER_SIZES", "cover_hues"]

C = tokens.MEDIA["cover"]
COVER_SHAPES = ("album", "book")
COVER_SIZES = ("hero", "tile", "mini")

#: The serifs covers are set in, nearest first (v70: Newsreader for albums, Literata for books).
ALBUM_SERIF = "Newsreader, Literata, Source Serif 4, Noto Serif, serif"
BOOK_SERIF = "Literata, Newsreader, Source Serif 4, Noto Serif, serif"


def cover_hues(title: str, hues: Sequence[float] | None = None) -> tuple[int, int, int]:
    """The cover's three hues: the given ones, or a stable set from the title."""
    if hues:
        values = [int(round(h)) % 360 for h in hues][:3]
        while len(values) < 3:
            values.append((values[0] + (C["hue_step_2"] if len(values) == 1 else C["hue_step_3"])) % 360)
        return values[0], values[1], values[2]
    import zlib

    base = zlib.crc32((title or "").strip().casefold().encode()) % 360
    return base, int(base + C["hue_step_2"]) % 360, int(base + C["hue_step_3"]) % 360


def _oklch(lightness: float, chroma: float, hue: float, alpha: float = 1.0) -> Gdk.RGBA:
    from .lumaui import oklch_rgba

    colour = Gdk.RGBA()
    colour.parse(oklch_rgba(lightness, chroma, hue, alpha))
    return colour


def _stops(*pairs: tuple[float, Gdk.RGBA]) -> list:
    stops = []
    for offset, colour in pairs:
        stop = Gsk.ColorStop()
        stop.offset, stop.color = offset, colour
        stops.append(stop)
    return stops


class CoverArt(Gtk.Widget):
    """An album or book cover: the real picture, or a deterministic typographic one.

    `flexible=True` lets a hero keep its appearance while fitting a smaller column.
    Its natural size and aspect ratio stay unchanged.
    Album `presentation="card"` and `"feature"` apply the visible v71 nested
    card captions; the default retains standalone cover typography.
    """

    __gtype_name__ = "LumaUICoverArt"
    __gsignals__ = {'artwork-loaded': (GObject.SignalFlags.RUN_LAST, None, (Gdk.Texture,))}

    def __init__(self, title: str, subtitle: str = "", *, picture: object = None,
                 hues: Sequence[float] | None = None, shape: str = "album", size: str = "tile", flexible: bool = False, presentation: str = "cover") -> None:
        if presentation not in ("cover", "card", "feature") or (shape != "album" and presentation != "cover"):
            raise ValueError("album presentation is cover, card or feature; books use cover")
        if shape not in COVER_SHAPES:
            raise ValueError(f"CoverArt shape must be one of {COVER_SHAPES}, not {shape!r}")
        if size not in COVER_SIZES:
            raise ValueError(f"CoverArt size must be one of {COVER_SIZES}, not {size!r}")
        super().__init__(css_name="lumaui-cover", overflow=Gtk.Overflow.VISIBLE,
                         halign=Gtk.Align.START if size != "tile" else Gtk.Align.FILL,
                         valign=Gtk.Align.START)
        self.shape, self.size = shape, size
        self.flexible = flexible
        self.presentation = presentation
        if presentation == "feature":
            self.set_opacity(C["feature_opacity_pct"] / 100)
        self.add_css_class(shape)
        self.add_css_class(size)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.title, self.subtitle = title or "", subtitle or ""
        self.hues = cover_hues(self.title, hues)
        self._picture: Gdk.Paintable | None = None
        self._source: str | None = None
        self._layouts: dict = {}
        self._preview = style._WidgetTexture(self, self._picture_arrived)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{self.title}, {self.subtitle}" if self.subtitle else self.title])
        self.set_picture(picture)

    # ── State ──
    def set_picture(self, picture: object) -> None:
        """Real art (a Gdk.Paintable, a path or a Gio.File); None draws the typographic cover."""
        self._preview.set_source(None, 0)
        self._picture = None
        self._source = None
        if isinstance(picture, Gio.File):
            picture = picture.get_path()
        if isinstance(picture, Gdk.Paintable):
            self._picture = picture
        elif picture:
            self._source = str(picture)
            side = self._natural() * max(1, self.get_scale_factor() or 1) * (1.5 if self.shape == "book" else 1)
            self._preview.set_source(self._source, side)
        self.queue_draw()

    def _picture_arrived(self, texture: Gdk.Texture | None) -> None:
        self._picture = texture
        self.queue_draw()
        if texture is not None:
            # Native consumers can light real artwork without duplicating decode
            # ownership. Generated art and source files retain their semantics.
            self.emit('artwork-loaded', texture)

    @property
    def generated(self) -> bool:
        """Whether the kit is drawing the cover (no real art yet)."""
        return self._picture is None

    # ── Layout ──
    def _natural(self) -> int:
        key = f"{self.shape}_{self.size}"
        return int(C[key])

    def _aspect(self) -> float:
        return 1.0 if self.shape == "album" else 2 / 3

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        natural = self._natural()
        if orientation == Gtk.Orientation.HORIZONTAL:
            # A flexible tile can also serve a compact now-playing row.
            # Its minimum is the existing mini role, not an unrelated 48px control.
            minimum = natural if self.size != "tile" and not self.flexible else min(natural, C[f"{self.shape}_mini"])
            return minimum, natural, -1, -1
        width = for_size if for_size > 0 else natural
        if self.size != "tile" and not self.flexible:
            width = natural
        height = int(math.ceil(width / self._aspect()))
        minimum = height
        if for_size < 0 and (self.size == "tile" or self.flexible):
            minimum = int(math.ceil(min(natural, C[f"{self.shape}_mini"]) / self._aspect()))
        return minimum, height, -1, -1

    def _box(self) -> Graphene.Rect:
        width = self.get_width()
        if self.size != "tile":
            width = min(width, self._natural())
        return style.rect(0, 0, width, width / self._aspect())

    def _shape(self, box: Graphene.Rect) -> Gsk.RoundedRect:
        if self.shape == "book":
            spine, edge = C["book_radius_spine"], C["book_radius_edge"]
            return style.rounded(box, spine, edge, edge, spine)
        radius = {"hero": C["album_radius_hero"], "tile": C["album_radius"], "mini": C["album_radius_mini"]}[self.size]
        return style.rounded(box, radius)

    # ── Drawing ──
    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._source:
            side = max(self._natural(), self.get_width()) * max(1, self.get_scale_factor()) * (1.5 if self.shape == "book" else 1)
            self._preview.set_source(self._source, side)
        if self.get_width() <= 0:
            return
        box = self._box()
        shape = self._shape(box)
        self._draw_edges(snapshot, shape, box)
        snapshot.push_rounded_clip(shape)
        if self._picture is not None:
            w, h = self._picture.get_intrinsic_width(), self._picture.get_intrinsic_height()
            target = style.cover_rect(w or box.get_width(), h or box.get_height(), box)
            if isinstance(self._picture, Gdk.Texture):
                snapshot.append_scaled_texture(self._picture, Gsk.ScalingFilter.TRILINEAR, target)
            else:
                snapshot.save()
                snapshot.translate(Graphene.Point().init(target.get_x(), target.get_y()))
                self._picture.snapshot(snapshot, target.get_width(), target.get_height())
                snapshot.restore()
        elif self.shape == "album":
            self._draw_album(snapshot, box)
        else:
            self._draw_book(snapshot, box)
        if self.shape == "book":
            self._draw_spine(snapshot, box)
        snapshot.pop()
        snapshot.append_border(shape, [0.5] * 4, [style.colour(self, "luma_media_cover_ring")] * 4)

    def _draw_edges(self, snapshot: Gtk.Snapshot, shape: Gsk.RoundedRect, box: Graphene.Rect) -> None:
        """The drop shadow, and a book's page edges behind the cover (v70 box-shadow lists)."""
        shadow = style.colour(self, "luma_media_cover_shadow")
        if self.size == "mini":
            snapshot.append_outset_shadow(shape, style.colour(self, "luma_media_cover_shadow", 0.5), 0, 1, 0, 2)
        elif self.size == "hero" and self.shape == "album":
            snapshot.append_outset_shadow(shape, shadow, 0, 28, -18, 54)
        else:
            snapshot.append_outset_shadow(shape, shadow, 0, 14, -12, 26)
        if self.shape == "book":
            pages = [style.colour(self, "luma_media_cover_page"), style.colour(self, "luma_media_cover_page_2")]
            edges = ((1, 2), (2, 3), (3, 4)) if self.size != "mini" else ((1, 1),)
            for index, (dx, dy) in reversed(list(enumerate(edges))):
                snapshot.append_outset_shadow(shape, pages[index % 2], dx, dy, -1 if self.size != "mini" else -0.5, 0)

    def _draw_album(self, snapshot: Gtk.Snapshot, box: Graphene.Rect) -> None:
        h, h2, h3 = self.hues
        w, hgt = box.get_width(), box.get_height()
        # linear-gradient(155deg, …): the line runs from 155° (clockwise from up) through the centre.
        angle = math.radians(155)
        dx, dy = math.sin(angle), -math.cos(angle)
        half = (abs(w * dx) + abs(hgt * dy)) / 2
        cx, cy = w / 2, hgt / 2
        start = Graphene.Point().init(cx - dx * half, cy - dy * half)
        end = Graphene.Point().init(cx + dx * half, cy + dy * half)
        snapshot.append_linear_gradient(box, start, end, _stops(
            (0.0, _oklch(C["top_lightness"], C["top_chroma"], h)),
            (1.0, _oklch(C["base_lightness"], C["base_chroma"], h3))))
        light = _oklch(C["light_lightness"], C["light_chroma"], h2)
        clear = light.copy()
        clear.alpha = 0
        centre = Graphene.Point().init(w * C["light_x_pct"] / 100, hgt * C["light_y_pct"] / 100)
        snapshot.append_radial_gradient(box, centre, w * C["light_w_pct"] / 100, hgt * C["light_h_pct"] / 100,
                                        0.0, 1.0, _stops((0.0, light), (C["light_stop_pct"] / 100, clear), (1.0, clear)))
        if self.size == "mini":
            return
        inset = w * C["inset_pct"] / 100
        on_media = style.colour(self, "luma_on_media")
        card, feature = self.presentation == "card", self.presentation == "feature"
        role = tokens.TYPE_SCALE["feature_title"] if feature else None
        title = self._layout("title", " ".join(self.title.split()) if card else self.title, role["size"] if feature else w * C["title_pct"] / 100,
                             role["weight"] if feature else 400, ALBUM_SERIF, italic=True,
                             tracking=role["tracking_em"] if feature else 0,
                             width=w * C["feature_title_width_pct"] / 100 if feature else 0 if card else w - 2 * inset)
        title.set_line_spacing(role["line_height"] if feature else 1.02)
        _ink, logical = title.get_pixel_extents()
        glow = style.colour(self, "luma_media_shadow", 0.2)
        y = hgt - hgt * C["title_bottom_pct"] / 100 - logical.height
        if feature:
            y -= C["feature_title_bottom_gap"]
        snapshot.push_blur(6)
        style.append_layout(snapshot, title, inset, y + 1, glow)
        snapshot.pop()
        style.append_layout(snapshot, title, inset, y, on_media)
        if self.subtitle:
            sub_size = C["feature_subtitle_size"] if feature else C["card_subtitle_size"] if card else w * C["subtitle_pct"] / 100
            sub = self._layout("subtitle", self.subtitle.upper(), sub_size, 600, None,
                               tracking=C["subtitle_tracking_em"], width=0 if card or feature else w - 2 * inset)
            _ink, sub_logical = sub.get_pixel_extents()
            y = hgt - hgt * C["subtitle_bottom_pct"] / 100 - sub_logical.height
            ink = style.colour(self, "luma_muted") if card else style.colour(self, "luma_on_media", 0.78 * (C["feature_opacity_pct"] / 100 if feature else 1))
            style.append_layout(snapshot, sub, inset, y, ink)

    def _book_colours(self) -> tuple[Gdk.RGBA, Gdk.RGBA]:
        h = self.hues[0]
        return (_oklch(C["book_ground_lightness"], C["book_ground_chroma"], h),
                _oklch(C["book_ink_lightness"], C["book_ink_chroma"], h))

    def _draw_book(self, snapshot: Gtk.Snapshot, box: Graphene.Rect) -> None:
        ground, ink = self._book_colours()
        w, hgt = box.get_width(), box.get_height()
        snapshot.append_color(ground, box)
        if self.size == "mini":
            return
        pad = w * C["book_padding_x_pct"] / 100
        title = self._layout("book-title", self.title, w * C["book_title_pct"] / 100, 500, BOOK_SERIF,
                             width=w - 2 * pad, centre=True)
        title.set_line_spacing(1.1)
        author = self._layout("book-author", self.subtitle.upper(), w * C["book_author_pct"] / 100, 400, BOOK_SERIF,
                              tracking=C["book_author_tracking_em"], width=w - 2 * pad, centre=True)
        _i, t = title.get_pixel_extents()
        _i, a = author.get_pixel_extents()
        gap = w * C["book_gap_pct"] / 100 if self.subtitle else 0
        total = t.height + gap + (a.height if self.subtitle else 0)
        y = (hgt - total) / 2
        style.append_layout(snapshot, title, pad, y, ink)
        if self.subtitle:
            faded = ink.copy()
            faded.alpha = 0.82
            style.append_layout(snapshot, author, pad, y + t.height + gap, faded)

    def _draw_spine(self, snapshot: Gtk.Snapshot, box: Graphene.Rect) -> None:
        """The spine's shade and the sheen (v70 .lfcov::after)."""
        w, hgt = box.get_width(), box.get_height()
        shade = style.colour(self, "luma_media_shadow", 0.18)
        sheen = style.colour(self, "luma_on_media", 0.07)
        clear = style.colour(self, "luma_on_media", 0.0)
        snapshot.append_linear_gradient(box, Graphene.Point().init(0, 0), Graphene.Point().init(w, 0),
                                        _stops((0.0, shade), (0.04, sheen), (0.09, clear), (1.0, clear)))
        gloss = style.colour(self, "luma_on_media", 0.1)
        angle = math.radians(160)
        dx, dy = math.sin(angle), -math.cos(angle)
        half = (abs(w * dx) + abs(hgt * dy)) / 2
        snapshot.append_linear_gradient(box, Graphene.Point().init(w / 2 - dx * half, hgt / 2 - dy * half),
                                        Graphene.Point().init(w / 2 + dx * half, hgt / 2 + dy * half),
                                        _stops((0.0, gloss), (0.4, clear), (1.0, clear)))
        edge = style.colour(self, "luma_on_media", 0.08)
        snapshot.append_color(edge, style.rect(0, 0, min(3.0, w * 0.02), hgt))

    def _layout(self, key: str, text: str, size: float, weight: int, family: str | None, *, italic: bool = False,
                tracking: float = 0.0, width: float = 0.0, centre: bool = False):
        cache_key = (key, text, round(size, 2), round(width, 1))
        if cache_key in self._layouts:
            return self._layouts[cache_key]
        layout = style.text_layout(self, text, max(1.0, size), weight)
        description = layout.get_font_description().copy()
        if family:
            description.set_family(family)
        if italic:
            description.set_style(Pango.Style.ITALIC)
        layout.set_font_description(description)
        if tracking:
            attrs = layout.get_attributes() or Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(int(tracking * size * Pango.SCALE)))
            layout.set_attributes(attrs)
        if width > 0:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_height(-3 if key.endswith("title") else -1)
        if centre:
            layout.set_alignment(Pango.Alignment.CENTER)
        if len(self._layouts) > 8:
            self._layouts.clear()
        self._layouts[cache_key] = layout
        return layout
