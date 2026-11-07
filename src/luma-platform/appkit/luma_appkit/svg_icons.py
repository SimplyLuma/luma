# SPDX-License-Identifier: Apache-2.0
"""An installed app's icon, drawn from its SVG at the size it shows.

    image = Gtk.Image.new_from_paintable(app_icon_paintable("org.projectluma.Notes", 48))

GTK hands a scalable app icon to the image loader sandbox, which renders the SVG at its own
size (Luma's app icons are 1024 square with blurs: about a second each on a Raspberry Pi 5)
while the main loop waits, and again for every new size. Here librsvg renders the same file
in this process at twice the size shown (a few milliseconds), cached per file and pixel size,
and the texture is drawn with mipmapped filtering. Icons that are not SVG, or a system without
librsvg's typelib, keep GTK's own paintable.
"""
from __future__ import annotations

import math
import os
import sys
from collections import OrderedDict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

try:
    gi.require_version("Rsvg", "2.0")
    from gi.repository import Rsvg  # noqa: E402
    import cairo  # noqa: E402
except (ImportError, ValueError):
    Rsvg = None

__all__ = ["app_icon_paintable"]

_CACHE: OrderedDict[tuple[str, int, int], Gdk.Texture] = OrderedDict()
_CACHE_SIZE = 192
_FORMAT = getattr(Gdk.MemoryFormat, "B8G8R8A8_PREMULTIPLIED" if sys.byteorder == "little" else "A8R8G8B8_PREMULTIPLIED")


def _pixels(side: float) -> int:
    """Twice the logical side (a 2x screen), up to a multiple of 8, so nearby sizes share a texture."""
    return max(16, int(math.ceil(side * 2 / 8)) * 8)


def _texture(path: str, pixels: int) -> Gdk.Texture | None:
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return None
    key = (path, stamp, pixels)
    texture = _CACHE.get(key)
    if texture is not None:
        _CACHE.move_to_end(key)
        return texture
    try:
        handle = Rsvg.Handle.new_from_file(path)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixels, pixels)
        viewport = Rsvg.Rectangle()
        viewport.x, viewport.y, viewport.width, viewport.height = 0, 0, pixels, pixels
        if not handle.render_document(cairo.Context(surface), viewport):
            return None
    except (GLib.Error, TypeError, cairo.Error):
        return None
    surface.flush()
    texture = Gdk.MemoryTexture.new(pixels, pixels, _FORMAT, GLib.Bytes.new(bytes(surface.get_data())),
                                    surface.get_stride())
    _CACHE[key] = texture
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return texture


class SvgIcon(GObject.Object, Gdk.Paintable):
    """A square SVG drawn at the size it is given, from a cached texture of twice that."""

    __gtype_name__ = "LumaUISvgIcon"

    def __init__(self, path: str, size: int) -> None:
        super().__init__()
        self.path = path
        self.size = size

    def do_get_intrinsic_width(self) -> int:
        return self.size

    def do_get_intrinsic_height(self) -> int:
        return self.size

    def do_get_intrinsic_aspect_ratio(self) -> float:
        return 1.0

    def do_get_flags(self) -> Gdk.PaintableFlags:
        return Gdk.PaintableFlags.STATIC_SIZE | Gdk.PaintableFlags.STATIC_CONTENTS

    def do_snapshot(self, snapshot: Gdk.Snapshot, width: float, height: float) -> None:
        side = min(width, height)
        texture = _texture(self.path, _pixels(side)) if side > 0 else None
        if texture is None:
            return
        bounds = Graphene.Rect().init((width - side) / 2, (height - side) / 2, side, side)
        snapshot.append_scaled_texture(texture, Gsk.ScalingFilter.TRILINEAR, bounds)


def app_icon_paintable(app_id: str | None, size: int, widget: Gtk.Widget | None = None) -> Gdk.Paintable | None:
    """The installed icon of `app_id` (a desktop file id, or an icon name) at `size`, or None."""
    if not app_id:
        return None
    display = (widget.get_display() if widget is not None else None) or Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    gicon = None
    try:
        info = Gio.DesktopAppInfo.new(app_id if app_id.endswith(".desktop") else f"{app_id}.desktop")
    except (TypeError, AttributeError):
        info = None
    if info is not None:
        gicon = info.get_icon()
    if gicon is None and theme.has_icon(app_id):
        gicon = Gio.ThemedIcon.new(app_id)
    if gicon is None:
        return None
    scale = max(1, widget.get_scale_factor()) if widget is not None else 1
    if Rsvg is not None:
        path = gicon.get_file().get_path() if isinstance(gicon, Gio.FileIcon) else None
        if path is None:
            found = theme.lookup_by_gicon(gicon, size, scale, Gtk.TextDirection.NONE, 0)
            file = found.get_file() if found is not None else None
            path = file.get_path() if file is not None else None
        if path and path.endswith(".svg") and _texture(path, _pixels(size)) is not None:
            return SvgIcon(path, size)
    return theme.lookup_by_gicon(gicon, size, scale, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.PRELOAD)
