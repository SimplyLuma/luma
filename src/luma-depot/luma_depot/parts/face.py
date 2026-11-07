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

__all__ = ["face", "face_colour", "hue_paint", "icon_path"]


def hue_paint(widget: Gtk.Widget, lightness: float, chroma: float, hue: float) -> None:
    """Paint `widget` an OKLCH colour from data (a monogram's hue, a reviewer's avatar)."""
    import re
    channels = [int(v) for v in re.findall(r"\d+", lumaui.oklch_rgba(lightness, chroma, hue))[:3]]
    widget.add_css_class("lumaui-painted")
    lumaui.paint_class(widget, "#%02x%02x%02x" % tuple(channels))

#: (size, radius) per name.
SIZES = {"xs": (26, 7), "row": (46, 12), "sm": (44, 11), "md": (64, 15), "lg": (76, 19)}
_LOOK_GLYPHS = {"Sounds": "volume-2", "Font": "type", "Cursor": "mouse-pointer-2", "Theme": "palette",
                "Icons": "blocks", "Appearance": "paintbrush"}


def icon_path(world: World, item: Item) -> str | None:
    """The file of an app's own icon (a Luma icon), when the world has one."""
    app = item.app
    if app is None or not app.icon_name or world.assets is None:
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
        return _sample(path)
    return None


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
        elif app.mark and world.assets is not None and (world.assets / "marks" / f"{app.mark}.svg").is_file():
            image = Gtk.Image.new_from_file(str(world.assets / "marks" / f"{app.mark}.svg"))
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
            picture = text
            box.add_css_class("mono")
            hue_paint(box, 0.55, 0.1, item.hue)
    elif look is not None:
        if look.art and world.assets is not None:
            art = world.assets / "art" / f"{look.art}.jpg"
            picture = Gtk.Picture.new_for_filename(str(art))
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
            lumaui.hue_class(box, look.hue)
    elif tweak is not None:
        picture = icons.image(tweak.icon)
        box.add_css_class("glyph")
        lumaui.hue_class(box, tweak.hue)
    if picture is not None:
        picture.set_hexpand(True)
        picture.set_vexpand(True)
        box.append(picture)
    box.set_name(f"face-{item.kind}-{item.id}")
    del radius
    return box


def path_exists(path: str | None) -> bool:
    return bool(path) and Path(path).is_file()
