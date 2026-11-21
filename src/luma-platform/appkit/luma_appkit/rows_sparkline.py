# SPDX-License-Identifier: Apache-2.0
"""LumaUI: a sparkline, a small line of recent values with a soft area under it.

    spark = Sparkline(capacity=60, label="Bitrate", tone="accent")
    spark.push(5800)                       # each sample; the oldest falls off past `capacity`
    spark.set_tone("warning")              # accent, warning or danger
    spark.mark()                           # a dot on the newest sample (a dropped frame)

The line and the area take the tone's colour from the sheet (light, dark and
high contrast); the scale is 0 to `max_value`, or to the largest value shown.
It fills its width (a phone drawer's row is enough) at `height` px; nothing
animates. Its accessible description is the label and the newest value.
"""
from __future__ import annotations

from collections import deque
from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402

from . import lumaui  # noqa: E402

__all__ = ["Sparkline", "SPARKLINE_TONES"]

SPARKLINE_TONES = ("accent", "warning", "danger")


class Sparkline(Gtk.Widget):
    __gtype_name__ = "LumaUISparkline"

    def __init__(self, values: Sequence[float] = (), *, capacity: int = 60, max_value: float | None = None,
                 tone: str = "accent", height: int = 24, label: str | None = None) -> None:
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.IMG)
        if capacity < 2:
            raise ValueError("a sparkline holds 2 values at least")
        self.add_css_class("lumaui-sparkline")
        self.capacity, self.max_value, self.height = capacity, max_value, height
        self._values: deque[float] = deque(maxlen=capacity)
        self._marks: deque[bool] = deque(maxlen=capacity)
        self._label = label
        self._tone = "accent"
        self.set_tone(tone)
        self.set_values(values)

    # ── data ──

    def push(self, value: float) -> None:
        self._values.append(float(value))
        self._marks.append(False)
        self._changed()

    def set_values(self, values: Sequence[float]) -> None:
        self._values.clear()
        self._marks.clear()
        for value in list(values)[-self.capacity:]:
            self._values.append(float(value))
            self._marks.append(False)
        self._changed()

    def mark(self) -> None:
        """A dot on the newest value (a drop, a reconnect)."""
        if self._marks:
            self._marks[-1] = True
            self.queue_draw()

    @property
    def values(self) -> list[float]:
        return list(self._values)

    def set_tone(self, tone: str) -> None:
        if tone not in SPARKLINE_TONES:
            raise ValueError(f"a sparkline's tone is one of {', '.join(SPARKLINE_TONES)}, not {tone!r}")
        lumaui.set_css_class(self, self._tone, False)
        self._tone = tone
        self.add_css_class(tone)
        self.queue_draw()

    def set_label(self, text: str | None) -> None:
        self._label = text
        self._describe()

    def _changed(self) -> None:
        self._describe()
        self.queue_draw()

    def _describe(self) -> None:
        last = f"{self._values[-1]:g}" if self._values else "no data"
        words = f"{self._label}: {last}" if self._label else last
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION, Gtk.AccessibleProperty.LABEL],
                             [words, self._label or "Recent values"])

    # ── drawing ──

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 48, 120, -1, -1
        return self.height, self.height, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        values = list(self._values)
        width, height = self.get_width(), self.get_height()
        if len(values) < 2 or width <= 0 or height <= 0:
            return
        color = self.get_color()
        top = self.max_value if self.max_value is not None else max(values)
        top = top if top > 0 else 1.0
        pad = 1.5  # half the line, so the line's top and bottom aren't clipped
        step = width / (self.capacity - 1)
        start = width - step * (len(values) - 1)  # newest at the right edge; a short history fills from there

        def point(i: int, v: float) -> tuple[float, float]:
            fraction = min(max(v / top, 0.0), 1.0)
            return start + step * i, pad + (height - 2 * pad) * (1 - fraction)

        line = Gsk.PathBuilder.new()
        area = Gsk.PathBuilder.new()
        x0, y0 = point(0, values[0])
        line.move_to(x0, y0)
        area.move_to(x0, height)
        area.line_to(x0, y0)
        for i, v in enumerate(values[1:], start=1):
            x, y = point(i, v)
            line.line_to(x, y)
            area.line_to(x, y)
        area.line_to(start + step * (len(values) - 1), height)
        area.close()
        soft = color.copy()
        soft.alpha = color.alpha * 0.16
        snapshot.append_fill(area.to_path(), Gsk.FillRule.WINDING, soft)
        stroke = Gsk.Stroke.new(1.5)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(line.to_path(), stroke, color)
        for i, marked in enumerate(self._marks):
            if marked:
                x, y = point(i, values[i])
                dot = Gsk.PathBuilder.new()
                dot.add_circle(Graphene.Point().init(x, y), 2.5)
                snapshot.append_fill(dot.to_path(), Gsk.FillRule.WINDING, color)
