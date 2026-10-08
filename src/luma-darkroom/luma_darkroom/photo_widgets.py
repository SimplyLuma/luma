# SPDX-License-Identifier: Apache-2.0
"""Darkroom-only photographic surfaces, composed with LumaUI controls.

The rating, colour labels and image canvas are Darkroom-only surfaces.
The photo grid and adjustment controls come from shared LumaUI media parts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk

from luma_appkit import apply_type, icons
from luma_appkit.action_center import register_item
from luma_appkit.media_transport import MediaGlyph



def _named(button: Gtk.Button, label: str) -> Gtk.Button:
    button.set_tooltip_text(label)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return button


class PhotoRating(Gtk.Box):
    """The app's five-star culling control (v70 `.drstars`)."""

    def __init__(self, value: int = 0, *, on_change: Callable[[int], None] | None = None,
                 readonly: bool = False, pixel_size: int = 16) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self.add_css_class("dr-rating")
        self.value = value
        self.on_change = on_change
        self.stars: list[Gtk.Widget] = []
        self.glyphs: list[Gtk.Stack] = []
        for n in range(1, 6):
            glyph = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
            glyph.add_named(MediaGlyph("star", pixel_size), "outline")
            glyph.add_named(MediaGlyph("star", pixel_size, filled=True), "filled")
            if readonly:
                star: Gtk.Widget = glyph
            else:
                button = _named(Gtk.Button(child=glyph), f"{n} star" + ("s" if n != 1 else ""))
                button.connect("clicked", lambda _b, rating=n: self.choose(rating))
                star = button
            star.add_css_class("dr-rating-star")
            self.append(star)
            self.stars.append(star)
            self.glyphs.append(glyph)
        self.set_value(value)

    def set_value(self, value: int) -> None:
        self.value = max(0, min(5, value))
        for n, (star, glyph) in enumerate(zip(self.stars, self.glyphs), 1):
            glyph.set_visible_child_name("filled" if n <= self.value else "outline")
            if n <= self.value:
                star.add_css_class("on")
            else:
                star.remove_css_class("on")

    def choose(self, value: int) -> None:
        next_value = 0 if value == self.value else value
        self.set_value(next_value)
        if self.on_change is not None:
            self.on_change(next_value)


class PhotoLabels(Gtk.Box):
    """Four colour labels in the culling bar (v70 `.drlabs`)."""

    COLOURS = ("red", "amber", "green", "blue")

    def __init__(self, value: str | None = None, *, on_change: Callable[[str | None], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("dr-labels")
        self.on_change = on_change
        self.buttons: dict[str, Gtk.Button] = {}
        for colour in self.COLOURS:
            button = _named(Gtk.Button(), f"{colour.title()} label")
            button.add_css_class("dr-label")
            button.add_css_class(colour)
            button.set_valign(Gtk.Align.CENTER)
            button.connect("clicked", lambda _b, key=colour: self.choose(key))
            self.append(button)
            self.buttons[colour] = button
        self.value = None
        self.set_value(value)

    def set_value(self, value: str | None) -> None:
        self.value = value
        for colour, button in self.buttons.items():
            if colour == value:
                button.add_css_class("on")
            else:
                button.remove_css_class("on")

    def choose(self, colour: str) -> None:
        next_value = None if self.value == colour else colour
        self.set_value(next_value)
        if self.on_change is not None:
            self.on_change(next_value)


# These two native controls are complete bar items. Let ActionCenter measure
# them with the rest of the row before placing the floating bar.
register_item(PhotoRating, lambda rating, _size: rating)
register_item(PhotoLabels, lambda labels, _size: labels)


class _BeforeHalf(Gtk.Widget):
    """Paint the original at full image size, clipped at the compare divider."""

    def __init__(self) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.paintable: Gdk.Paintable | None = None
        self.fraction = 0.5

    def do_measure(self, _orientation, _for_size):
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self.paintable is None:
            return
        width, height = self.get_width(), self.get_height()
        snapshot.push_clip(Graphene.Rect().init(0, 0, width * self.fraction, height))
        self.paintable.snapshot(snapshot, width, height)
        snapshot.pop()

    def set_paintable(self, paintable: Gdk.Paintable | None) -> None:
        self.paintable = paintable
        self.queue_draw()


class PhotoCanvas(Gtk.Overlay):
    """Darkroom's image stage; actual pixels come from RasterEngine."""

    def __init__(self) -> None:
        super().__init__()
        self.set_name("dr-pic")
        self.add_css_class("dr-canvas")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.edited = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN)
        self.edited.set_size_request(1, 1)
        self.edited.set_can_shrink(True)
        self.edited.set_hexpand(True)
        self.edited.set_vexpand(True)
        self.set_child(self.edited)
        self.original = _BeforeHalf()
        self.add_overlay(self.original)
        self.original.set_visible(False)
        self.compare = False
        self.split = 0.5
        self.split_line = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.split_line.set_draw_func(self._draw_split)
        self.split_line.set_can_target(False)
        self.split_line.set_visible(False)
        self.add_overlay(self.split_line)
        split_icon = icons.image("split")
        split_icon.set_pixel_size(16)
        split_icon.set_halign(Gtk.Align.CENTER)
        split_icon.set_valign(Gtk.Align.CENTER)
        self.split_handle = Gtk.Button(child=split_icon)
        self.split_handle.add_css_class("dr-split-handle")
        self.split_handle.update_property([Gtk.AccessibleProperty.LABEL],
                                          ["Move before and after divider"])
        self.split_handle.set_visible(False)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._split_begin)
        drag.connect("drag-update", self._split_update)
        self.split_handle.add_controller(drag)
        self.add_overlay(self.split_handle)
        self.crop = False
        self.crop_guides = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.crop_guides.set_draw_func(self._draw_crop)
        self.crop_guides.set_can_target(False)
        self.crop_guides.set_visible(False)
        self.add_overlay(self.crop_guides)
        self.before_label = Gtk.Label(label="Before")
        self.after_label = Gtk.Label(label="After")
        for label in (self.before_label, self.after_label):
            apply_type(label, "caption")
            label.add_css_class("dr-compare-label")
            label.set_visible(False)
            self.add_overlay(label)
        self.connect("get-child-position", self._position_compare_label)
        self._source: Path | None = None
        self._heal_handler: Callable[[float, float], None] | None = None
        click = Gtk.GestureClick()
        click.connect("pressed", self._pressed)
        self.add_controller(click)

    def set_heal_handler(self, callback: Callable[[float, float], None] | None) -> None:
        self._heal_handler = callback

    def _pressed(self, _gesture: Gtk.GestureClick, _count: int, x: float, y: float) -> None:
        if self._heal_handler is None:
            return
        rect = self._image_rect()
        if rect is None:
            return
        left, top, width, height = rect
        if left <= x <= left + width and top <= y <= top + height:
            self._heal_handler((x - left) / width, (y - top) / height)

    def _image_rect(self) -> tuple[float, float, float, float] | None:
        paintable = self.edited.get_paintable()
        if paintable is None:
            return None
        iw, ih = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        if iw <= 0 or ih <= 0:
            return None
        scale = min(self.get_width() / iw, self.get_height() / ih)
        width, height = iw * scale, ih * scale
        return ((self.get_width() - width) / 2, (self.get_height() - height) / 2,
                width, height)

    def set_source(self, path: Path) -> None:
        self._source = path
        try:
            self.original.set_paintable(Gdk.Texture.new_from_filename(str(path)))
        except (GLib.Error, OSError):
            self.original.set_paintable(None)
        self.edited.set_file(Gio.File.new_for_path(str(path)))

    def set_original_texture(self, texture: Gdk.Paintable) -> None:
        self.original.set_paintable(texture)

    def set_preview(self, data: bytes) -> None:
        self.edited.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
        if self.get_parent() is not None:
            self.get_parent().queue_allocate()
        self.crop_guides.queue_draw()

    def set_compare(self, compare: bool) -> None:
        self.compare = compare
        self.original.set_visible(compare)
        self.before_label.set_visible(compare)
        self.after_label.set_visible(compare)
        self.split_line.set_visible(compare)
        self.split_handle.set_visible(compare)

    def _split_begin(self, _gesture, _x: float, _y: float) -> None:
        self._split_start = self.split

    def _split_update(self, _gesture, x: float, _y: float) -> None:
        self.split = max(0.02, min(0.98, self._split_start + x / max(1, self.get_width())))
        self.original.fraction = self.split
        self.original.queue_draw()
        self.split_line.queue_draw()
        self.queue_allocate()

    def _draw_split(self, _area, cr, _width: int, height: int) -> None:
        ink = self.get_color()
        cr.set_source_rgba(ink.red, ink.green, ink.blue, ink.alpha)
        x = self.get_width() * self.split
        cr.rectangle(x - 1, 0, 2, height)
        cr.fill()

    def _position_compare_label(self, _overlay, child: Gtk.Widget, allocation: Gdk.Rectangle) -> bool:
        if child is self.split_handle:
            _minimum, width, _baseline, _natural_baseline = child.measure(Gtk.Orientation.HORIZONTAL, -1)
            _minimum, height, _baseline, _natural_baseline = child.measure(Gtk.Orientation.VERTICAL, width)
            allocation.x = round(self.get_width() * self.split - width / 2)
            allocation.y = round((self.get_height() - height) / 2)
            allocation.width, allocation.height = width, height
            return True
        if child not in (self.before_label, self.after_label):
            return False
        rect = self._image_rect()
        if rect is None:
            return False
        left, top, width, _height = rect
        _minimum, natural_width, _baseline, _natural_baseline = child.measure(Gtk.Orientation.HORIZONTAL, -1)
        _minimum, natural_height, _baseline, _natural_baseline = child.measure(Gtk.Orientation.VERTICAL, natural_width)
        allocation.x = round(left + 12 if child is self.before_label else left + width - natural_width - 12)
        allocation.y = round(top + 12)
        allocation.width, allocation.height = natural_width, natural_height
        return True

    def set_crop(self, crop: bool) -> None:
        self.crop = crop
        self.crop_guides.set_visible(crop)
        self.crop_guides.queue_draw()

    def _draw_crop(self, _area, cr, _width: int, _height: int) -> None:
        rect = self._image_rect()
        if rect is None:
            return
        left, top, width, height = rect
        ink = self.get_color()
        cr.set_source_rgba(ink.red, ink.green, ink.blue, ink.alpha * 0.75)
        for fraction in (1 / 3, 2 / 3):
            cr.move_to(left + width * fraction, top)
            cr.line_to(left + width * fraction, top + height)
            cr.move_to(left, top + height * fraction)
            cr.line_to(left + width, top + height * fraction)
        cr.set_line_width(1)
        cr.stroke()
        cr.rectangle(left, top, width, height)
        cr.set_line_width(2)
        cr.stroke()
