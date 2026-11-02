# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: wide tiles, shelves, tile grids, sectioned pages, the progress button,
the screenshot viewer and the TV empty state.

Any app gets a 16:9 TV tile without TV art (`TvWideTile`), in this order:

1. the app's own wide tile, installed as the icon `<app-id>-tv` (shipped art,
   design-center `icons/luma-tv-*.svg`);
2. composed at runtime from its v4 icon with `tools/wide-tile.py`'s geometry
   (`compose_wide_tile`): the plate on the whole tile, the art between plate and
   rim centred at 1.15x, the rim;
3. its brand mark (its icon) on its brand colour;
4. its name on a plate in its category's hue.

The corner is a share of the tile (rx 190 of 1600 x 900: 11.875% / 21.111%), never a
fixed radius, so the art and the frame agree at every size. Art tiles carry their own
rim; only drawn plates get the top glint. The focused tile lifts 1.06 with a white ring
on the art alone, never the caption.

    tile = TvWideTile("Tide", app_id="org.projectluma.Tide", icon=gicon, meta="Installed")
    shelf = TvShelf([tile, ...])                     # a row: slides only as far as needed
    grid = TvTileGrid([tile, ...])                   # five columns edge to edge, 52 between rows
    page = TvPage(column)                            # a page of sections; sections that left fade
    page.add_section(section)
    focus_changed: page.reveal(widget); shelf.reveal(widget)
    button = TvProgressButton("Get", icon="download")  # fills with progress, then reads Open
    TvScreenshotViewer.open(focus, paths, index)     # full screen; Left/Right; Back closes
    TvEmptyState("No updates", "Every app is up to date.", icon="check")

Images are decoded off the main loop (`media_style.TextureLoader`) when a tile is first
laid out, never up front for a whole grid.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import icons  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .tv_media import TvSlide, _UnitFollower, _animate, _at, _black, _lift_ms, _stop, _clear  # noqa: E402
from .tv_parts import TvButton  # noqa: E402
from .tv_stage import add_tv_style, tv_fade, tv_material  # noqa: E402

__all__ = ["TvWideTile", "TvShelf", "TvTileGrid", "TvPage", "TvProgressButton", "TvShot", "TvScreenshotViewer",
           "TvEmptyState", "compose_wide_tile", "wide_tile_art", "CATEGORY_HUES"]

TV = tokens.TV
TILE = TV["tile"]
# The wide tile's art: 1600 x 900, plate rx 190 (tools/wide-tile.py).
ART_W, ART_H, ART_R = 1600, 900, 190
CORNER_X, CORNER_Y = TILE["corner_x"], TILE["corner_y"]
#: A category's hue for a tile drawn from the app's name (the kit's category colours).
CATEGORY_HUES = {"create": 25, "work": 250, "media": 330, "play": 150, "tools": 200}
#: Five tiles across the safe width with equal gaps (the TV home's Apps grid).
GRID_COLUMNS = 5
GRID_ROW_GAP = TILE["row_gap"]
SHELF_GAP = 36
#: Captioned tiles (a name and a line under each, as an app list's) run four across at the
#: shelf's gap, 46 between rows: the caption already separates the rows.
CAPTIONED_COLUMNS, CAPTIONED_ROW_GAP = 4, 46
SHELF_TILE = TV["cover"]["landscape"]
SHELF_PEEK = 0.12 * (TV["layout"]["base_width"] - 2 * TV["layout"]["safe_x"])


def _sheet(opaque: bool) -> str:
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    return f"""
.tv-tile-name {{ font-size: 22tv; font-weight: 650; line-height: 27tv; color: {m('ink')}; margin-top: 14tv; }}
.tv-tile-meta {{ font-size: 18tv; font-weight: 600; line-height: 22tv; color: {m('ink_3')}; margin-top: 4tv; }}
.lumaui-tv-stage label.tv-tile-plate-name {{ font-size: 40tv; font-weight: 750; letter-spacing: -0.8tv; line-height: 48tv; color: white; }}
.tv-tile-meta.tv-ok {{ color: #86d39c; }}
.tv-shelf-row {{ border-spacing: {SHELF_GAP}tv; }}
.tv-tile-grid {{ border-spacing: {GRID_ROW_GAP}tv {GRID_ROW_GAP}tv; }}
.tv-tile-grid.captioned {{ border-spacing: {SHELF_GAP}tv {CAPTIONED_ROW_GAP}tv; }}
.tv-tile-meta-line {{ margin-top: 4tv; }}
.tv-tile-meta-line .tv-tile-meta {{ margin-top: 0; }}
.tv-tile-meta-line image {{ -gtk-icon-size: 18tv; color: #86d39c; margin-bottom: 4tv; }}
.tv-progress-btn {{ min-width: {220 - 2 * TV['button']['padding_x']}tv; }}
.tv-shot-viewer {{ background-color: rgba(0, 0, 0, .85); }}
.tv-empty {{ padding: 64tv 40tv 56tv 40tv; border-radius: 30tv; border-spacing: 0; }}
.tv-empty-disc {{ min-width: 112tv; min-height: 112tv; border-radius: 32tv; }}
.tv-empty-disc image {{ -gtk-icon-size: 54tv; color: {m('ink_2')}; }}
.tv-empty-title {{ font-size: 34tv; font-weight: 700; letter-spacing: -0.68tv; color: {m('ink')}; margin-top: 28tv; }}
.tv-empty-text {{ font-size: 24tv; font-weight: 500; color: {m('ink_2')}; margin-top: 10tv; }}
.tv-empty-actions {{ border-spacing: 14tv; margin-top: 32tv; }}
"""


_styled = False


def _style() -> None:
    global _styled
    if not _styled and Gdk.Display.get_default() is not None:
        _styled = True
        add_tv_style(_sheet)


# ── The art: own tile, composed tile ────────────────────────────────────────

def _grab_group(body: str, gid: str) -> str | None:
    start = body.find(f'<g id="{gid}"')
    if start < 0:
        return None
    depth, j = 0, start
    while True:
        opening, closing = body.find("<g", j + 1), body.find("</g>", j + 1)
        if closing < 0:
            return None
        if 0 <= opening < closing:
            depth, j = depth + 1, opening
        else:
            if depth == 0:
                return body[start:closing + 4]
            depth, j = depth - 1, closing


def compose_wide_tile(svg: str) -> str | None:
    """A 1600 x 900 wide tile from a v4 icon's SVG, as tools/wide-tile.py makes one; None when the
    icon has no plate (it is not a v4 icon).

    The plate's fills cover the whole tile at rx 190, everything between the plate and its rim is
    the art, centred at 1.15x, and the rim is redrawn on the tile's edge. An icon without a rim
    keeps none (nothing is added).
    """
    if "<defs>" not in svg or "</defs>" not in svg:
        return None
    defs = svg[svg.index("<defs>"):svg.index("</defs>") + 7]
    clip = re.search(r'<clipPath id="([^"]+)"><rect x="82" y="82" width="860" height="860" rx="202"/></clipPath>', defs)
    cid = clip.group(1) if clip else None
    if clip:
        defs = defs.replace(clip.group(0), f'<clipPath id="{cid}"><rect width="{ART_W}" height="{ART_H}" rx="{ART_R}"/></clipPath>')
    body = svg[svg.index("</defs>") + 7:]
    plate = _grab_group(body, "plate")
    if plate is None:
        return None
    fills = re.findall(r'fill="url\(#([^)]+)\)"', plate)
    if not fills:
        return None
    rim_group = _grab_group(body, "plate-rim")
    rim = re.search(r'stroke="url\(#([^)]+)\)"', rim_group) if rim_group else None
    after = body.index(plate) + len(plate)
    end = body.index('<g id="plate-rim"') if rim_group else body.rindex("</svg>")
    art = body[after:end]
    if cid:
        art = art.replace(f'clip-path="url(#{cid})"', "")
    tx, ty = ART_W / 2 - 512 * 1.15, ART_H / 2 - 512 * 1.15
    plates = "".join(f'<rect width="{ART_W}" height="{ART_H}" rx="{ART_R}" fill="url(#{f})"/>' for f in fills)
    rim_svg = ""
    if rim is not None:
        clip_attr = f' clip-path="url(#{cid})"' if cid else ""
        rim_svg = (f'<g id="plate-rim"{clip_attr}><rect width="{ART_W}" height="{ART_H}" rx="{ART_R}" fill="none" '
                   f'stroke="url(#{rim.group(1)})" stroke-width="8"/></g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ART_W} {ART_H}" width="{ART_W}" height="{ART_H}">\n'
            f'{defs}\n<g id="plate">{plates}</g>\n<g id="art" transform="translate({tx:.1f} {ty:.1f}) scale(1.15)">{art}</g>\n'
            f'{rim_svg}\n</svg>\n')


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "luma" / "tv-tiles"


def _icon_file(icon: Gio.Icon | str | None, size: int = 256) -> str | None:
    """The file an icon resolves to in the current theme (or the icon's own file)."""
    if icon is None:
        return None
    if isinstance(icon, str):
        if icon.startswith("/"):
            return icon if os.path.isfile(icon) else None
        icon = Gio.ThemedIcon.new(icon)
    if isinstance(icon, Gio.FileIcon):
        path = icon.get_file().get_path()
        return path if path and os.path.isfile(path) else None
    display = Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    if isinstance(icon, Gio.ThemedIcon) and not any(theme.has_icon(n) for n in icon.get_names()):
        return None
    paintable = theme.lookup_by_gicon(icon, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
    file = paintable.get_file() if paintable is not None else None
    path = file.get_path() if file is not None else None
    return path if path and os.path.isfile(path) else None


def wide_tile_art(app_id: str | None, icon: Gio.Icon | str | None) -> tuple[str, str] | None:
    """Where an app's wide tile comes from: ("own", path) for its installed `<app-id>-tv` icon,
    ("composed", path) for a tile composed from its v4 icon (cached on disk), or None."""
    if app_id:
        own = _icon_file(f"{app_id}-tv", 512)
        if own:
            return "own", own
    source = _icon_file(icon, 512)
    if not source or not source.endswith(".svg"):
        return None
    try:
        stat = os.stat(source)
        digest = hashlib.sha256(f"{source}\0{stat.st_mtime_ns}\0{stat.st_size}\0v1".encode()).hexdigest()[:24]
        target = _cache_dir() / f"{digest}.svg"
        if target.is_file():
            return "composed", str(target)
        composed = compose_wide_tile(Path(source).read_text(encoding="utf-8"))
        if composed is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(composed, encoding="utf-8")
        temporary.replace(target)
        return "composed", str(target)
    except (OSError, UnicodeDecodeError):
        return None


def _parse_colour(value: str | None) -> Gdk.RGBA | None:
    if not value:
        return None
    colour = Gdk.RGBA()
    return colour if colour.parse(value) else None


def _oklch(lightness: float, chroma: float, hue: float, alpha: float = 1.0) -> Gdk.RGBA:
    from .lumaui import oklch_rgba
    colour = Gdk.RGBA()
    colour.parse(oklch_rgba(lightness, chroma, hue, alpha))
    return colour


def _white(alpha: float) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.red = colour.green = colour.blue = 1.0
    colour.alpha = alpha
    return colour


def _shade(colour: Gdk.RGBA, factor: float) -> Gdk.RGBA:
    value = colour.copy()
    value.red, value.green, value.blue = (max(0.0, min(1.0, c * factor)) for c in (colour.red, colour.green, colour.blue))
    return value


def _tile_rect(w: float, h: float) -> Gsk.RoundedRect:
    rect = Gsk.RoundedRect()
    corner = Graphene.Size().init(w * CORNER_X, h * CORNER_Y)
    rect.init(Graphene.Rect().init(0, 0, w, h), corner, corner, corner, corner)
    return rect


# ── The wide tile ───────────────────────────────────────────────────────────

class _TileArt(_UnitFollower, Gtk.Widget):
    """The 16:9 art of a wide tile (see TvWideTile); draws itself, its only child the name a plate carries."""

    __gtype_name__ = "LumaUITvTileArt"

    def __init__(self, name: str, kind: str, source: str | None, brand: Gdk.RGBA | None, hue: float,
                 width: float | None, lift: bool) -> None:
        super().__init__(overflow=Gtk.Overflow.VISIBLE, can_target=False)
        self.name, self.kind, self._source = name, kind, source
        self._brand, self._hue, self._width, self._lift = brand, hue, width, lift
        self._texture: Gdk.Texture | None = None
        self._request = None
        self._requested = 0
        self._scale, self._focus, self._focused = 1.0, 0.0, False
        self._animation = None
        self.plate_label = None
        if kind == "name":  # the plate's name is words, as the mockup's: 40/750, centred, at most two lines
            self.plate_label = Gtk.Label(label=name, justify=Gtk.Justification.CENTER, wrap=True,
                                         wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                                         ellipsize=Pango.EllipsizeMode.END)
            self.plate_label.add_css_class("tv-tile-plate-name")
            self.plate_label.set_parent(self)

    def do_dispose(self) -> None:
        if self.plate_label is not None:
            self.plate_label.unparent()
            self.plate_label = None
        Gtk.Widget.do_dispose(self)

    def set_tv_focus(self, on: bool) -> None:
        if on == self._focused:
            return
        self._focused = on
        if not self._lift:
            return
        start = self._focus

        def step(value: float) -> None:
            self._focus = value
            self._scale = 1.0 + (TILE["focus_scale"] - 1.0) * value
            self.queue_draw()
        self._animation = _animate(self, start, 1.0 if on else 0.0, _lift_ms(), step, self._animation)

    def do_root(self) -> None:
        Gtk.Widget.do_root(self)
        self._follow_stage()

    def do_unroot(self) -> None:
        self._leave_stage()
        self._cancel()
        Gtk.Widget.do_unroot(self)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        u = self._unit()
        if orientation == Gtk.Orientation.HORIZONTAL:
            if self._width is not None:
                w = int(round(self._width * u))
                return w, w, -1, -1
            return 0, int(round(SHELF_TILE * u)), -1, -1
        width = for_size if for_size > 0 else int(round((self._width or SHELF_TILE) * u))
        h = int(round(width * 9 / 16))
        return h, h, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        if self.plate_label is not None:
            w = int(width * 0.8)
            h = min(height, self.plate_label.measure(Gtk.Orientation.VERTICAL, w)[1])
            self.plate_label.allocate(w, h, -1, Gsk.Transform.new().translate(
                Graphene.Point().init((width - w) / 2, (height - h) / 2)))
        if self._source:
            self._load(height if self.kind != "mark" else height * 0.44)

    def _load(self, side: float) -> None:
        from .media_style import TextureLoader
        bucket = TextureLoader.bucket(side)
        if self._texture is not None and self._requested >= bucket:
            return
        if self._requested == bucket and self._request is not None:
            return
        self._cancel()
        self._requested = bucket
        source = self._source

        def arrived(texture: Gdk.Texture | None) -> None:
            self._request = None
            if texture is not None and source == self._source:
                self._texture = texture
                self.queue_draw()
        self._request = TextureLoader.shared().request(source, side, arrived)

    def _cancel(self) -> None:
        if self._request is not None:
            self._request.cancel()
            self._request = None

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        u = self._unit()
        w, h = float(self.get_width()), float(self.get_height())
        art = _tile_rect(w, h)
        lifted = abs(self._scale - 1.0) > 1e-3
        if lifted:
            snapshot.save()
            snapshot.translate(Graphene.Point().init(w / 2, h / 2))
            snapshot.scale(self._scale, self._scale)
            snapshot.translate(Graphene.Point().init(-w / 2, -h / 2))
        t = self._focus
        snapshot.append_outset_shadow(art, _black(0.45 + 0.15 * t), 0, (14 + 10 * t) * u, 0, (30 + 20 * t) * u)
        if t > 1e-3:
            snapshot.append_outset_shadow(art, _white(t), 0, 0, TILE["ring"] * u, 0)
        snapshot.push_rounded_clip(art)
        bounds = Graphene.Rect().init(0, 0, w, h)
        if self.kind in ("own", "composed"):
            if self._texture is not None:
                snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.TRILINEAR, bounds)
            else:
                snapshot.append_color(_oklch(0.32, 0.01, 265), bounds)
        else:
            self._plate(snapshot, bounds, w, h, u)
        snapshot.pop()
        if self.kind in ("mark", "name"):
            # drawn plates get the glint the art tiles carry in their own rim
            snapshot.append_inset_shadow(art, _white(0.14), 0, 1.5 * u, 0, 0)
        if lifted:
            snapshot.restore()

    def _plate(self, snapshot: Gtk.Snapshot, bounds: Graphene.Rect, w: float, h: float, u: float) -> None:
        if self.kind == "mark" and self._brand is not None:
            snapshot.append_color(self._brand, bounds)
        else:
            top, bottom = _oklch(0.5, 0.1, self._hue), _oklch(0.3, 0.08, self._hue)
            snapshot.append_linear_gradient(bounds, Graphene.Point().init(w * 0.33, 0), Graphene.Point().init(w * 0.67, h),
                                            [_stop(0, top), _stop(1, bottom)])
        light = _white(0.16)
        snapshot.append_radial_gradient(bounds, Graphene.Point().init(0.26 * w, 0.12 * h), 0.8 * w, 0.9 * h, 0, 1,
                                        [_stop(0, light), _stop(0.6, _clear(light))])
        if self.kind == "mark" and self._texture is not None:
            side = h * 0.44
            snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.TRILINEAR,
                                           Graphene.Rect().init((w - side) / 2, (h - side) / 2, side, side))
        elif self.kind == "name" and self.plate_label is not None:
            self.snapshot_child(self.plate_label, snapshot)


class TvWideTile(Gtk.Box):
    """An app's 16:9 TV tile with an optional caption (name, and a quiet line under it).

    `width` in TV units fixes the tile's width (a shelf's 400); without it the tile takes the
    width it is given (a grid's column). `art` is a wide tile file when the caller already has
    one; otherwise the tile looks for the app's own (`<app_id>-tv`), then composes one from
    `icon`, then draws `icon` on `brand` (a CSS colour), then the name on its `category`'s hue.
    `set_tv_focus(True)` lifts the art 1.06 with the white ring (never the caption); `room`
    says what colours the room behind it (a file, or hues).
    """

    __gtype_name__ = "LumaUITvWideTile"

    def __init__(self, name: str, *, app_id: str | None = None, icon: Gio.Icon | str | None = None,
                 art: str | None = None, brand: str | None = None, category: str | None = None,
                 hue: float | None = None, width: float | None = None, caption: bool = True,
                 meta: str = "", meta_ok: bool = False, meta_icon: str | None = None,
                 accessible: str | None = None, lift: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START,
                         halign=Gtk.Align.START if width is not None else Gtk.Align.FILL,
                         accessible_role=Gtk.AccessibleRole.BUTTON if lift else Gtk.AccessibleRole.IMG)
        _style()
        self.add_css_class("tv-wide-tile")
        self.name = name
        brand_colour = _parse_colour(brand)
        hue = float(hue) if hue is not None else float(CATEGORY_HUES.get((category or "").casefold(), 250))
        found = ("own", art) if art else wide_tile_art(app_id, icon)
        kind = found[0] if found else ("mark" if brand_colour is not None and _icon_file(icon) else "name")
        source = found[1] if found else (_icon_file(icon, 256) if kind == "mark" else None)
        self.kind = kind
        self.art = _TileArt(name, kind, source, brand_colour, hue, width, lift)
        self.append(self.art)
        self.name_label = self.meta_label = None
        if caption:
            self.name_label = Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
            self.name_label.add_css_class("tv-tile-name")
            self.append(self.name_label)
            if meta:
                self.meta_label = Gtk.Label(label=meta, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                            single_line_mode=True)
                self.meta_label.add_css_class("tv-tile-meta")
                if meta_ok:
                    self.meta_label.add_css_class("tv-ok")
                if meta_icon:
                    line = Gtk.Box()
                    line.add_css_class("tv-tile-meta-line")
                    line.append(icons.image(meta_icon))
                    line.append(self.meta_label)
                    self.append(line)
                else:
                    self.append(self.meta_label)
        self.update_property([Gtk.AccessibleProperty.LABEL], [accessible or " · ".join(x for x in (name, meta) if x)])

    @property
    def room(self) -> tuple[str | None, tuple[int, int, int] | None]:
        """What colours the room for this tile: its art (own or composed), or its plate's hues."""
        art = self.art
        if art.kind in ("own", "composed"):
            return art._source, None
        hue = _rgb_hue(art._brand) if art.kind == "mark" and art._brand is not None else int(art._hue)
        return None, (hue, hue, (hue + 20) % 360)

    def set_meta(self, text: str) -> None:
        if self.meta_label is not None:
            self.meta_label.set_label(text)

    def set_tv_focus(self, on: bool) -> None:
        self.art.set_tv_focus(on)


def _rgb_hue(colour: Gdk.RGBA) -> int:
    import colorsys
    return int(colorsys.rgb_to_hsv(colour.red, colour.green, colour.blue)[0] * 360)


# ── Shelves, grids and pages ────────────────────────────────────────────────

class TvShelf(Gtk.Box):
    """A horizontal row of wide tiles that slides only as far as needed to bring the focused one
    in, with a peek of the next. Tiles are 400 wide (the landscape cover) unless given a width."""

    __gtype_name__ = "LumaUITvShelf"

    def __init__(self, tiles: Sequence[Gtk.Widget] = ()) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        _style()
        self.row = Gtk.Box()
        self.row.add_css_class("tv-shelf-row")
        for tile in tiles:
            self.row.append(tile)
        self.slide = TvSlide(self.row, orientation=Gtk.Orientation.HORIZONTAL, clip=False, peek=SHELF_PEEK)
        self.append(self.slide)

    def append_tile(self, tile: Gtk.Widget) -> None:
        self.row.append(tile)

    def reveal(self, widget: Gtk.Widget) -> None:
        self.slide.reveal(widget)


class TvTileGrid(Gtk.Grid):
    """Columns of wide tiles filling the safe width edge to edge (five at 16:9), 52 between rows;
    `captioned` tiles (name and line) four across, 36 apart, 46 between rows.
    Up and Down move by row (spatial focus finds the tile above or below); Up from the first row
    reaches the tabs."""

    __gtype_name__ = "LumaUITvTileGrid"

    def __init__(self, tiles: Sequence[Gtk.Widget] = (), *, columns: int | None = None,
                 captioned: bool = False) -> None:
        super().__init__(column_homogeneous=True, hexpand=True)
        _style()
        self.add_css_class("tv-tile-grid")
        if captioned:
            self.add_css_class("captioned")
        columns = columns or (CAPTIONED_COLUMNS if captioned else GRID_COLUMNS)
        self.columns = columns
        self.count = 0
        for tile in tiles:
            self.append_tile(tile)
        # a short last row keeps the columns' width
        for index in range(len(tiles), columns):
            self.attach(Gtk.Box(), index, 0, 1, 1)

    def append_tile(self, tile: Gtk.Widget) -> None:
        tile.set_valign(Gtk.Align.START)
        self.attach(tile, self.count % self.columns, self.count // self.columns, 1, 1)
        self.count += 1


class TvPage(TvSlide):
    """A page of sections that follows the remote: the section holding the focus comes to the top
    (the first section never moves past its place), the page stops short at its end so the rows
    above stay in view, and a section fades only once it has actually left the view."""

    __gtype_name__ = "LumaUITvPage"

    def __init__(self, child: Gtk.Widget, *, top_pad: float = 26) -> None:
        super().__init__(child, orientation=Gtk.Orientation.VERTICAL, lead=top_pad)
        self.sections: list[Gtk.Widget] = []
        self._faded: set[Gtk.Widget] = set()

    def add_section(self, widget: Gtk.Widget) -> Gtk.Widget:
        self.sections.append(widget)
        return widget

    def reveal(self, widget: Gtk.Widget, *, animate: bool = True) -> None:
        if self.child is None or (widget is not self.child and not widget.is_ancestor(self.child)):
            return
        if self.get_width() <= 0 or self.get_height() <= 0:
            # not laid out yet (a page just built): reveal once it is, without animating
            def later(*_args) -> bool:
                if self.get_width() > 0 and self.get_height() > 0:
                    self.reveal(widget, animate=False)
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            self.add_tick_callback(lambda _w, _c: later())
            return
        section = next((s for s in self.sections if widget is s or widget.is_ancestor(s)), None)
        if section is None:
            super().reveal(widget, animate=animate)
            self._fade()
            return
        ok, bounds = section.compute_bounds(self.child)
        if not ok:
            return
        u = self._unit()
        view = self.get_height()
        content = self._content(self.get_width(), view)
        first = self.sections and section is self.sections[0]
        target = 0.0 if first else bounds.get_y() - self.lead * u
        target = max(0.0, min(target, max(0.0, content - view)))
        self.slide_to(target, animate=animate)
        self._target = target
        self._fade(target)

    def _fade(self, offset: float | None = None) -> None:
        offset = self.offset if offset is None else offset
        u = self._unit()
        if self.get_height() <= 0:
            return
        for section in self.sections:
            ok, bounds = section.compute_bounds(self.child)
            if not ok or bounds.get_height() <= 0:
                continue
            gone = bounds.get_y() + bounds.get_height() - offset < self.lead * u * 0.5
            if gone != (section in self._faded):
                (self._faded.add if gone else self._faded.discard)(section)
                tv_fade(section, 0.0 if gone else 1.0, 250)


# ── The progress button ─────────────────────────────────────────────────────

class TvProgressButton(TvButton):
    """One button that is the whole install: Get, then it fills with progress ("Installing 40%"),
    then it reads Open. The suggested (accent) button; there is one bright thing.

    `set_progress(fraction, label)` shows a fill; `set_progress(None, label)` clears it and sets
    the label (Open, Update, Try Again).
    """

    __gtype_name__ = "LumaUITvProgressButton"

    def __init__(self, label: str, *, icon: str | None = None) -> None:
        super().__init__(label, icon=icon, accent=True)
        _style()
        self.add_css_class("tv-progress-btn")
        self.get_first_child().set_hexpand(True)  # the words sit in the middle of its 220
        self.fraction: float | None = None
        self._icon = self.get_first_child().get_first_child() if icon else None

    def set_progress(self, fraction: float | None, label: str | None = None) -> None:
        self.fraction = None if fraction is None else max(0.0, min(1.0, fraction))
        if label is not None:
            self.label.set_label(label)
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if self._icon is not None:
            self._icon.set_visible(self.fraction is None)
        self.queue_draw()

    def set_icon(self, name: str | None) -> None:
        inner = self.get_first_child()
        if self._icon is not None:
            inner.remove(self._icon)
            self._icon = None
        if name:
            self._icon = icons.image(name)
            inner.prepend(self._icon)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self.fraction is not None and self.get_width() > 0:
            w, h = self.get_width(), self.get_height()
            unit = self._unit()
            rect = Gsk.RoundedRect()
            rect.init_from_rect(Graphene.Rect().init(0, 0, w, h), min(TV["button"]["radius"] * unit, h / 2))
            snapshot.push_rounded_clip(rect)
            snapshot.append_color(_white(0.22), Graphene.Rect().init(0, 0, w * self.fraction, h))
            snapshot.pop()
        child = self.get_first_child()
        while child is not None:
            self.snapshot_child(child, snapshot)
            child = child.get_next_sibling()

    def _unit(self) -> float:
        node = self.get_parent()
        while node is not None and node.find_property("unit") is None:
            node = node.get_parent()
        return float(node.get_property("unit")) if node is not None else 1.0


# ── The screenshot viewer ───────────────────────────────────────────────────

class TvShot(_UnitFollower, Gtk.Widget):
    """A screenshot in a row: 320 tall in one of two shapes, a desktop's 16:10 or a phone's 9:19.5,
    whichever the picture is closer to (the catalogue's `aspect` when it gives one, else the
    picture's own once it arrives), filled edge to edge; radius 22 on black, the lift and white
    ring with the focus."""

    __gtype_name__ = "LumaUITvShot"
    HEIGHT, RADIUS = 320, 22
    WIDE, TALL = 16 / 10, 9 / 19.5

    @classmethod
    def shape(cls, aspect: float) -> float:
        return cls.WIDE if aspect >= 1 else cls.TALL

    def __init__(self, path: str, *, aspect: float | None = None, accessible: str = "") -> None:
        super().__init__(overflow=Gtk.Overflow.VISIBLE, accessible_role=Gtk.AccessibleRole.BUTTON)
        self.path, self.aspect = path, self.shape(aspect or self.WIDE)
        self._texture: Gdk.Texture | None = None
        self._request = None
        self._scale, self._focus, self._focused, self._animation = 1.0, 0.0, False, None
        if accessible:
            self.update_property([Gtk.AccessibleProperty.LABEL], [accessible])

    def do_root(self) -> None:
        Gtk.Widget.do_root(self)
        self._follow_stage()
        if self._texture is None and self._request is None:
            from .media_style import TextureLoader
            self._request = TextureLoader.shared().request(self.path, 640, self._arrived)

    def do_unroot(self) -> None:
        self._leave_stage()
        if self._request is not None:
            self._request.cancel()
            self._request = None
        Gtk.Widget.do_unroot(self)

    def _arrived(self, texture: Gdk.Texture | None) -> None:
        self._request = None
        if texture is None:
            return
        self._texture = texture
        aspect = self.shape(texture.get_width() / max(1, texture.get_height()))
        if aspect != self.aspect:
            self.aspect = aspect
            self.queue_resize()
        self.queue_draw()

    def set_tv_focus(self, on: bool) -> None:
        if on == self._focused:
            return
        self._focused = on

        def step(value: float) -> None:
            self._focus = value
            self._scale = 1.0 + 0.04 * value
            self.queue_draw()
        self._animation = _animate(self, self._focus, 1.0 if on else 0.0, _lift_ms(), step, self._animation)

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        h = round(self.HEIGHT * self._unit())
        size = h if orientation == Gtk.Orientation.VERTICAL else round(h * self.aspect)
        return size, size, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        u = self._unit()
        w, h = float(self.get_width()), float(self.get_height())
        box = Graphene.Rect().init(0, 0, w, h)
        shape = Gsk.RoundedRect()
        shape.init_from_rect(box, self.RADIUS * u)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(w / 2, h / 2))
        snapshot.scale(self._scale, self._scale)
        snapshot.translate(Graphene.Point().init(-w / 2, -h / 2))
        t = self._focus
        snapshot.append_outset_shadow(shape, _black(0.45 + 0.15 * t), 0, (14 + 10 * t) * u, 0, (30 + 20 * t) * u)
        if t > 1e-3:
            snapshot.append_outset_shadow(shape, _white(t), 0, 0, 5 * u, 0)
        snapshot.push_rounded_clip(shape)
        snapshot.append_color(_black(1.0), box)
        if self._texture is not None:
            from .media_style import cover_rect
            snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.TRILINEAR,
                                           cover_rect(self._texture.get_width(), self._texture.get_height(), box))
        snapshot.pop()
        snapshot.restore()


class TvScreenshotViewer(Gtk.Widget):
    """A screenshot full screen over a dark scrim: Left/Right move through them, Back closes and
    returns the focus to the screenshot that opened it. Open with `TvScreenshotViewer.open`."""

    __gtype_name__ = "LumaUITvScreenshotViewer"

    def __init__(self, paths: Sequence[str], index: int) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.IMG)
        _style()
        self.add_css_class("tv-shot-viewer")
        self.paths = list(paths)
        self.index = max(0, min(index, len(self.paths) - 1))
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.picture.set_parent(self)
        self.picture.set_focusable(True)
        self._show()

    @classmethod
    def open(cls, focus, paths: Sequence[str], index: int = 0,
             on_change: Callable[[int], None] | None = None) -> "TvScreenshotViewer | None":
        stage = focus.root
        if not paths:
            return None
        viewer = cls(paths, index)
        viewer._focus, viewer._stage, viewer._on_change = focus, stage, on_change
        stage.place(viewer, fill=True)
        stage.raise_child(viewer)
        viewer.set_opacity(0)
        tv_fade(viewer, 1.0, 250)
        focus.add(viewer.picture, group="tv-shot-viewer", on_key=viewer._step, on_activate=lambda: None)
        focus.push_scope([viewer.picture], on_back=viewer._back)
        return viewer

    def _show(self) -> None:
        path = self.paths[self.index]
        try:
            self.picture.set_paintable(Gdk.Texture.new_from_filename(path))
        except GLib.Error:
            self.picture.set_paintable(None)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"Screenshot {self.index + 1} of {len(self.paths)}"])

    def _step(self, direction: str) -> bool:
        from .tv_focus import LEFT, RIGHT
        if direction in (LEFT, RIGHT):
            index = self.index + (1 if direction == RIGHT else -1)
            if 0 <= index < len(self.paths):
                self.index = index
                self._show()
                if self._on_change is not None:
                    self._on_change(index)
        return True

    def _back(self) -> bool:
        self.close()
        return True

    def close(self) -> None:
        if self.get_parent() is None:
            return
        self._focus.remove(self.picture)
        self._focus.pop_scope()
        self._stage.remove(self)

    def do_dispose(self) -> None:
        if self.picture is not None and self.picture.get_parent() is self:
            self.picture.unparent()
        self.picture = None

    def do_measure(self, _orientation, _for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        w, h = int(width * 0.8), int(height * 0.8)
        self.picture.allocate(w, h, -1, _at((width - w) / 2, (height - h) / 2))


# ── The empty state ─────────────────────────────────────────────────────────

class TvEmptyState(Gtk.Box):
    """The TV empty state (offline, nothing found, nothing to update): a bar card with the 112 icon
    tile and soft light behind it, a 34/700 title, one 24 sentence and optional TV buttons
    (`buttons`, to register with the focus; the first is suggested)."""

    __gtype_name__ = "LumaUITvEmptyState"

    def __init__(self, title: str, description: str = "", *, icon: str = "info",
                 actions: Sequence[tuple[str, Callable[[], None]]] = ()) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        _style()
        for name in ("tv-empty", "tv-bar"):
            self.add_css_class(name)
        disc = Gtk.CenterBox(halign=Gtk.Align.CENTER)
        for name in ("tv-empty-disc", "tv-chip"):
            disc.add_css_class(name)
        disc.set_center_widget(icons.image(icon))
        self.append(disc)
        heading = Gtk.Label(label=title, justify=Gtk.Justification.CENTER, wrap=True)
        heading.add_css_class("tv-empty-title")
        self.append(heading)
        if description:
            text = Gtk.Label(label=description, justify=Gtk.Justification.CENTER, wrap=True,
                             wrap_mode=Pango.WrapMode.WORD, max_width_chars=48)
            text.add_css_class("tv-empty-text")
            self.append(text)
        self.buttons: list[tuple[TvButton, Callable[[], None]]] = []
        if actions:
            row = Gtk.Box(halign=Gtk.Align.CENTER)
            row.add_css_class("tv-empty-actions")
            for index, (label, callback) in enumerate(actions):
                button = TvButton(label, accent=index == 0)
                row.append(button)
                self.buttons.append((button, callback))
            self.append(row)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}. {description}".strip()])
