# SPDX-License-Identifier: MPL-2.0
"""Disks-only proportion map and benchmark plot, coloured from LumaUI tokens."""
from __future__ import annotations

import math

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

from luma_appkit import TypeLabel

from .model import DriveView, display_size as size_text, map_weights, volume_rows


TONES = {tone: 'luma_storage_tone_' + tone
         for tone in ('blue', 'violet', 'amber', 'green', 'pink', 'slate')}
TONES['free'] = 'luma_ink_secondary'



class Swatch(Gtk.DrawingArea):
    """Decorative v70 square in the volume list and card."""

    def __init__(self, tone: str) -> None:
        super().__init__(content_width=14, content_height=14,
                         valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.tone = tone
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        _rounded(cr, 0, 0, width, height, 5)
        if self.tone != 'free':
            _fill(cr, _rgba(self, TONES.get(self.tone, 'luma_ink_secondary')))
            cr.fill()
            return
        cr.save()
        cr.clip()
        _fill(cr, _rgba(self, 'luma_muted'))
        cr.set_line_width(2)
        for at in range(-height, width + height, 4):
            cr.move_to(at, height)
            cr.line_to(at + height, 0)
        cr.stroke()
        cr.restore()
        _rounded(cr, .5, .5, width - 1, height - 1, 5)
        _fill(cr, _rgba(self, 'luma_muted'))
        cr.set_line_width(1)
        cr.stroke()


def _rgba(widget: Gtk.Widget, token: str):
    found, color = widget.get_style_context().lookup_color(token)
    return color if found else widget.get_color()


def _fill(cr, color, alpha: float = 1.0) -> None:
    cr.set_source_rgba(color.red, color.green, color.blue, color.alpha * alpha)


def _rounded(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = max(0, min(radius, width / 2, height / 2))
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _segment_outline(cr, width: float, height: float, radius: float,
                     *, first: bool, last: bool) -> None:
    """Round the outside of a drive only; adjoining partition ends stay square."""
    radius = max(0, min(radius, width / 2, height / 2))
    cr.new_sub_path()
    cr.move_to(radius if first else 0, 0)
    cr.line_to(width - radius if last else width, 0)
    if last:
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
    cr.line_to(width, height - radius if last else height)
    if last:
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
    cr.line_to(radius if first else 0, height)
    if first:
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
    cr.line_to(0, radius if first else 0)
    if first:
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


class StorageMap(Gtk.Overlay):
    """App-only proportion map with a real button for each volume segment."""

    def __init__(self, drive: DriveView, selected: str, choose) -> None:
        super().__init__(hexpand=True)
        self.drive, self.selected, self.choose = drive, selected, choose
        self.volumes = volume_rows(drive)
        self.weights = map_weights(self.volumes, drive.size)
        self.set_name('dsk-map')
        self.add_css_class('disks-map')
        self.canvas = Gtk.DrawingArea(content_height=60, hexpand=True,
                                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.canvas.set_can_target(False)
        self.canvas.set_draw_func(self._draw)
        self.set_child(self.canvas)
        self.hits = Gtk.Fixed(hexpand=True, vexpand=True)
        self.add_overlay(self.hits)
        self.set_measure_overlay(self.hits, False)
        self._laid_out_size = None
        self.buttons = []
        for index, volume in enumerate(self.volumes):
            button = Gtk.Button()
            button.add_css_class('disks-map-hit')
            button.add_css_class('disks-tone-'+volume.tone)
            if volume.key == self.selected:
                button.add_css_class('selected')
            spoken = f'{volume.name} · {size_text(volume.size)}'
            button.set_tooltip_text(spoken)
            button.connect('clicked', lambda _b, key=volume.key: self.choose(key))
            art = Gtk.DrawingArea(accessible_role=Gtk.AccessibleRole.PRESENTATION)
            art.set_can_target(False)
            art.set_draw_func(lambda _a, cr, width, height, v=volume, i=index:
                              self._draw_segment(cr, width, height, v, i))
            label = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            label.set_halign(Gtk.Align.START)
            label.set_valign(Gtk.Align.START)
            label.set_margin_start(12)
            label.set_margin_top(7)
            name = TypeLabel(volume.name, role='body', weight=650)
            name.label.add_css_class('disks-map-name')
            label.append(name)
            size = TypeLabel(size_text(volume.size), role='small')
            size.label.add_css_class('disks-map-size')
            label.append(size)
            text_host = Gtk.Overlay(child=art)
            text_host.add_overlay(label)
            text_host.set_measure_overlay(label, False)
            button.set_child(text_host)
            button.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
            self.hits.put(button, 0, 0)
            self.buttons.append((button, label))

    def _spans(self, width: float):
        inside = max(0, width - 8)
        usable = max(0, inside - 3 * max(0, len(self.volumes) - 1))
        x = 4.
        for volume, weight in zip(self.volumes, self.weights):
            segment_width = weight * usable
            yield volume, x, segment_width
            x += segment_width + 3

    def _place_buttons(self, width: int, height: int) -> bool:
        if self.get_parent() is None:
            return False
        self._laid_out_size = (width, height)
        for (button, label), (_, left, span) in zip(self.buttons, self._spans(width)):
            self.hits.move(button, round(left), 4)
            wide = span / max(1, width) > .13
            label.set_visible(wide)
            button.set_size_request(max(1, round(span)), max(1, height - 8))
        return False

    def _draw(self, _area, cr, width, height):
        if self._laid_out_size != (width, height):
            GLib.idle_add(self._place_buttons, width, height)

    def _draw_segment(self, cr, width: int, height: int, volume, index: int) -> None:
        cr.save()
        _segment_outline(cr, width, height, 13, first=index == 0,
                         last=index == len(self.volumes) - 1)
        cr.clip()
        if volume.tone == 'free':
            _fill(cr, _rgba(self, 'luma_ink'), .08)
            for at in range(-height, width + height, 8):
                cr.move_to(at, height)
                cr.line_to(at + height, 0)
            cr.set_line_width(1)
            cr.stroke()
        elif volume.used is not None and volume.size > 0:
            _fill(cr, _rgba(self, 'luma_storage_map_used_' + volume.tone))
            cr.rectangle(0, 0, width * min(1, volume.used / volume.size), height)
            cr.fill()
        cr.restore()


class SpeedGraph(Gtk.DrawingArea):
    """Disks-only read/write sample plot. Empty curves are honest on live data."""

    def __init__(self, read: tuple[float, ...] = (), write: tuple[float, ...] = ()) -> None:
        super().__init__(content_height=130, hexpand=True)
        self.read, self.write = read, write
        self.set_name('dsk-speed-graph')
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Speed test samples'])
        self.set_draw_func(self._draw)

    def set_samples(self, read: tuple[float, ...], write: tuple[float, ...] = ()) -> None:
        self.read, self.write = read, write
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        _rounded(cr, 0, 0, width, height, 14)
        _fill(cr, _rgba(self, 'luma_well'))
        cr.fill()
        peak = max((*self.read, *self.write, 1)) * 1.2
        for values, token in ((self.read, 'luma_blue'), (self.write, 'luma_green')):
            if len(values) < 2:
                continue
            _fill(cr, _rgba(self, token))
            cr.set_line_width(2)
            for index, value in enumerate(values):
                x = index * width / max(1, len(values) - 1)
                y = height - 8 - value / peak * (height - 16)
                if index == 0:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
            cr.stroke()
