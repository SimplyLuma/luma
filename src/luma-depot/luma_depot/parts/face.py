# SPDX-License-Identifier: Apache-2.0
"""An item's face: its icon, an official logo on its own colour, or a monogram on a hue from its id.

Looks show their picture, swatches, D-Ink's specimen or their kind's glyph; Tweaks their glyph on a
hue (the mockup's `face()`). Sizes are the mockup's: xs 26, sm 44/46, md 64, lg 76.
"""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from luma_appkit import icons, lumaui

from ..model.world import Item, World

__all__ = ["face", "face_colour", "hue_paint", "icon_path", "picture"]

def picture(path: str | None) -> Gtk.Picture:
    """A picture from a file, decoded through the kit's texture cache (WebP included)."""
    from luma_appkit.media_framed import texture
    tex = texture(path)
    return Gtk.Picture.new_for_paintable(tex) if tex is not None else Gtk.Picture()


load_picture = picture


class Backdrop(Gtk.Widget):
    """A photo drawn to cover its box that asks for no size of its own (a card's blurred ground)."""

    __gtype_name__ = "DepotBackdrop"

    def __init__(self, path: str | None) -> None:
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        from luma_appkit.media_framed import texture
        self.texture = texture(path)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot) -> None:
        if self.texture is None:
            return
        gi.require_version("Graphene", "1.0")
        gi.require_version("Gsk", "4.0")
        from gi.repository import Graphene, Gsk
        w, h = self.get_width(), self.get_height()
        tw, th = self.texture.get_width(), self.texture.get_height()
        scale = max(w / tw, h / th)
        dw, dh = tw * scale, th * scale
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR,
                                       Graphene.Rect().init((w - dw) / 2, (h - dh) / 2, dw, dh))



def hue_paint(widget: Gtk.Widget, lightness: float, chroma: float, hue: float) -> None:
    """Paint `widget` an OKLCH colour from data (a monogram's hue, a reviewer's avatar)."""
    import re
    channels = [int(v) for v in re.findall(r"\d+", lumaui.oklch_rgba(lightness, chroma, hue))[:3]]
    widget.add_css_class("lumaui-painted")
    lumaui.paint_class(widget, "#%02x%02x%02x" % tuple(channels))

#: (size, radius) per name.
SIZES = {"xs": (26, 7), "row": (46, 12), "sm": (44, 11), "md": (64, 15), "mdc": (56, 13), "lg": (76, 19)}
_LOOK_GLYPHS = {"Sounds": "volume-2", "Font": "type", "Cursor": "mouse-pointer-2", "Theme": "palette",
                "Icons": "blocks", "Appearance": "paintbrush"}


def icon_path(world: World, item: Item) -> str | None:
    """The file of an app's own icon (a Luma icon), when the world has one."""
    app = item.app
    if app is None:
        return None
    if app.icon_url:
        return world.media_path(app.icon_url)
    if not app.icon_name or world.assets is None:
        return None
    path = world.assets / "icons" / f"{app.icon_name}.svg"
    return str(path) if path.is_file() else None


def face_colour(world: World, item: Item) -> str | None:
    """The colour the header glows in (8.2): a mark's ground, or the hue sampled from a Luma icon."""
    app = item.app
    if app is not None and app.mark_colour:
        return app.mark_colour
    path = icon_path(world, item)
    if path is not None:
        sampled = _sample(path)
        if sampled:
            return sampled
    # a monogram, a Look's or a Tweak's glyph: the hue it is drawn in
    import re
    channels = [int(v) for v in re.findall(r"\d+", lumaui.oklch_rgba(0.62, 0.13, item.hue))[:3]]
    return "#%02x%02x%02x" % tuple(channels)


def _sample(path: str) -> str | None:
    """The icon's most saturated colour, averaged (the mockup shell's sampleHue)."""
    try:
        texture = Gdk.Texture.new_from_filename(path)
    except Exception:  # noqa: BLE001
        return None
    from gi.repository import GdkPixbuf
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, 24, 24)
    except Exception:  # noqa: BLE001
        return None
    del texture
    data, channels, stride = pixbuf.get_pixels(), pixbuf.get_n_channels(), pixbuf.get_rowstride()
    total = [0.0, 0.0, 0.0]
    weight = 0.0
    for y in range(pixbuf.get_height()):
        for x in range(pixbuf.get_width()):
            i = y * stride + x * channels
            r, g, b = data[i], data[i + 1], data[i + 2]
            a = data[i + 3] if channels == 4 else 255
            spread = (max(r, g, b) - min(r, g, b)) / 255 * a / 255
            total[0] += r * spread
            total[1] += g * spread
            total[2] += b * spread
            weight += spread
    if weight < 1:
        return None
    return "#%02x%02x%02x" % tuple(round(v / weight) for v in total)


_gradients: dict[str, str] = {}
_provider: list = []


def paint_gradient(widget: Gtk.Widget, start: tuple[float, float, float], end: tuple[float, float, float],
                   degrees: int = 160) -> None:
    """Paint `widget` a linear gradient between two OKLCH colours from data (a glyph face's hue)."""
    from gi.repository import Gdk
    # OKLCH stops, blended in Oklab as CSS does between non-legacy colours (an sRGB blend is lighter mid-way).
    a, b = ("oklch(%.4g %.4g %.4g)" % (l, c, h % 360) for l, c, h in (start, end))
    name = "dq-grad-%d-%s" % (degrees, "-".join("%d" % round(v * 1000) for v in (*start, *end)))
    widget.add_css_class(name)
    if name not in _gradients:
        _gradients[name] = f".{name} {{ background: linear-gradient({degrees}deg in oklab, {a}, {b}); }}"
        display = Gdk.Display.get_default()
        if display is not None:
            if not _provider:
                provider = Gtk.CssProvider()
                Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
                _provider.append(provider)
            _provider[0].load_from_string("\n".join(_gradients.values()))


def _mark_path(world: World, mark: str) -> str | None:
    if "://" in mark:
        return world.media_path(mark)
    if world.assets is not None and (world.assets / "marks" / f"{mark}.svg").is_file():
        return str(world.assets / "marks" / f"{mark}.svg")
    return None


def face(world: World, item: Item, size: str = "row") -> Gtk.Widget:
    pixels, radius = SIZES[size]
    box = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN,
                  hexpand=False, vexpand=False)
    box.add_css_class("dq-face")
    box.add_css_class(f"f-{size}")
    box.set_size_request(pixels, pixels)
    app, look, tweak = item.app, item.look, item.tweak
    picture: Gtk.Widget | None = None
    if app is not None:
        path = icon_path(world, item)
        if path is not None:
            picture = Gtk.Image.new_from_file(path)
            picture.set_pixel_size(pixels)
            box.add_css_class("pic")
        elif app.mark and _mark_path(world, app.mark):
            image = Gtk.Image.new_from_file(_mark_path(world, app.mark))
            image.set_pixel_size(round(pixels * 0.58))
            picture = image
            box.add_css_class("mark")
            box.add_css_class("lumaui-painted")
            lumaui.paint_class(box, app.mark_colour)
        elif app.icon_name and "/" not in app.icon_name and not (world.assets and world.assets.exists()):
            image = Gtk.Image(icon_name=app.icon_name, pixel_size=pixels)
            picture = image
        else:
            text = Gtk.Label(label=item.name[:2])
            text.add_css_class("dq-mono")
            # The initials never widen the face (overflow: hidden): they float over it.
            picture = Gtk.Overlay(child=Gtk.Box())
            picture.add_overlay(text)
            box.add_css_class("mono")
            hue_paint(box, 0.55, 0.1, item.hue)
    elif look is not None:
        if look.art and world.assets is not None:
            art = world.assets / "art" / f"{look.art}.jpg"
            picture = load_picture(str(art))
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_can_shrink(True)
            picture.set_size_request(pixels, pixels)
            box.add_css_class("pic")
        elif look.swatch:
            grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True, hexpand=True, vexpand=True)
            grid.add_css_class("dq-swatch")
            for index, colour in enumerate(look.swatch[:4]):
                cell = Gtk.Box()
                cell.add_css_class("lumaui-painted")
                lumaui.paint_class(cell, colour)
                grid.attach(cell, index % 2, index // 2, 1, 1)
            picture = grid
            box.add_css_class("sw")
        elif look.ink:
            text = Gtk.Label(label="Aa")
            picture = text
            box.add_css_class("ink")
        else:
            picture = icons.image(_LOOK_GLYPHS.get(look.kind, "palette"))
            box.add_css_class("glyph")
            paint_gradient(box, (0.72, 0.13, look.hue), (0.5, 0.13, look.hue + 30))
    elif tweak is not None:
        picture = icons.image(tweak.icon)
        box.add_css_class("glyph")
        paint_gradient(box, (0.72, 0.13, tweak.hue), (0.5, 0.13, tweak.hue + 30))
    if picture is not None:
        picture.set_hexpand(True)
        picture.set_vexpand(True)
        box.append(picture)
    box.set_name(f"face-{item.kind}-{item.id}")
    del radius
    return box


def path_exists(path: str | None) -> bool:
    return bool(path) and Path(path).is_file()
