# SPDX-License-Identifier: Apache-2.0
"""The crop frame sits on the photo, never on the gray margin around it.

A wide photo in a taller canvas leaves a margin above and below it. Framing
a crop shows the whole photo, the frame and its handles map onto the photo's
rectangle, and nothing is painted on the margin. Picking another tool shows
the cropped result again.
"""
from __future__ import annotations

import os
import tempfile
import time
from io import BytesIO
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk

from luma_darkroom.application import DarkroomApplication
from luma_darkroom.engine import load_pillow
from luma_darkroom.model import Crop
from luma_darkroom.window import DarkroomWindow

context = GLib.MainContext.default()


def pump(seconds: float = .1) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(.005)


def wait(predicate, what: str) -> None:
    end = time.monotonic() + 20
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError(f"timed out: {what}")
        pump(.05)


def snapshot(window: Gtk.Window, widget=None):
    widget = widget or window
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, widget.get_width(), widget.get_height())
    # Use window coordinates rather than the shadow-expanded node bounds.
    viewport = Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height())
    texture = window.get_renderer().render_texture(snap.to_node(), viewport)
    return texture


def main() -> int:
    Image, *_ = load_pillow()
    output = Path(os.environ.get("DARKROOM_CROP_OUTPUT", tempfile.mkdtemp(prefix="darkroom-crop-")))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="darkroom-crop-") as temporary:
        photo = Path(temporary) / "wide.png"
        Image.new("RGB", (1800, 600), (40, 150, 70)).save(photo)
        app = DarkroomApplication()
        app.register(None)
        window = DarkroomWindow(app)
        window.set_default_size(1180, 820)
        window.present()
        pump(.3)
        window.open_path(photo)
        wait(lambda: window.document is not None and window.edited_picture.get_paintable() is not None, "photo rendered")

        crop = Crop(**{field: getattr(window.document.crop, field) for field in window.document.crop.__dataclass_fields__})
        crop.left, crop.top, crop.right, crop.bottom = .1, .1, .9, .9
        window.editor.set_crop(crop)
        window._schedule_render()
        wait(lambda: window.edited_picture.get_paintable().get_intrinsic_width() < 1800 * .85, "cropped preview")

        # The canvas margin as it looks with no crop frame on screen.
        pump(.3)
        found, origin = window.crop_overlay.compute_point(window.canvas_overlay, Graphene.Point().init(0, 0))
        assert found
        plain = Image.open(BytesIO(snapshot(window, window.canvas_overlay).save_to_png_bytes().get_data())).convert("RGB")
        scale = plain.width / window.canvas_overlay.get_width()
        margin = plain.getpixel((round(window.crop_overlay.get_width() / 2 * scale), round(4 * scale)))

        window._select_tool("crop")
        wait(lambda: window.edited_picture.get_paintable().get_intrinsic_width() == 1800, "whole photo while framing")
        pump(.3)
        left, top, width, height = window._image_rect()
        overlay_width, overlay_height = window.crop_overlay.get_width(), window.crop_overlay.get_height()
        assert abs(width / height - 3.0) < .02, (width, height)
        assert height < overlay_height - 40, "the wide photo leaves a margin above and below"
        assert 0 <= left and left + width <= overlay_width + .5 and 0 <= top and top + height <= overlay_height + .5

        # Both captures use canvas-local coordinates. Resolve the overlay
        # within that canvas rather than adding the window header origin.
        found, origin = window.crop_overlay.compute_point(window.canvas_overlay, Graphene.Point().init(0, 0))
        assert found
        texture = snapshot(window)
        texture.save_to_png(str(output / "crop-frame.png"))
        rendered = Image.open(BytesIO(snapshot(window, window.canvas_overlay).save_to_png_bytes().get_data())).convert("RGB")

        def pixel(x: float, y: float):
            return rendered.getpixel((round(x * scale), round(y * scale)))

        # The margin is neither shaded nor drawn on: the old frame spread the
        # shade over it and put the handles and frame line on it. The margin
        # band above the photo, away from the canvas's rounded edges, is
        # pixel-identical to the canvas with no crop frame.
        assert pixel(left + width / 2, top / 2) == margin, (pixel(left + width / 2, top / 2), margin)
        changed = []
        for y in range(6, int(top) - 2, 3):
            for x in range(int(left) + 24, int(left + width) - 24, 16):
                point = (round((origin.x + x) * scale), round((origin.y + y) * scale))
                if rendered.getpixel(point) != plain.getpixel(point):
                    changed.append((x, y, plain.getpixel(point), rendered.getpixel(point)))
        assert not changed, changed[:5]
        # Inside the photo the crop's handle bar is drawn at the crop corner.
        corner = pixel(left + width * .1 + 6, top + height * .1 + 1)
        assert min(corner) > 200, corner
        # Outside the crop, inside the photo, the shade darkens the photo.
        shaded = pixel(left + width * .05, top + height / 2)
        assert shaded[1] < 150 * .7, shaded

        window._select_tool("select")
        wait(lambda: window.edited_picture.get_paintable().get_intrinsic_width() < 1800 * .85, "cropped again")
        window.close()
        pump(.2)
    print(f"crop frame: photo at {left:.0f},{top:.0f} {width:.0f}x{height:.0f} in {overlay_width}x{overlay_height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
