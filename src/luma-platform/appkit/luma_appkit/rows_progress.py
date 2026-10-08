# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: ProgressLine, how far along something is (DT3).

    ProgressLine(0.42)                                    # a 4 px line in the accent (a row, a call's voicemail)
    ProgressLine(0.3, size="tile", tone="neutral")        # Leaf's book tile: 3 px in the quiet ink
    ProgressLine(0.66, size="meter", label="Memory")      # Monitor's meter
    ProgressLine(0.5, size="hero", label="Downloading")   # Depot's system update: 8 px in a well
    ProgressLine(2/5, tone="good", label="Steps")         # Tasks' steps done
    ProgressLine(0.7, size="well", label="Black toner")   # Settings' toner: 5 px in a well (v70 .lprog)

v70: `.mnmeter`, `.tkstepbar`, `.pnvbar` (4 px, 2 round, on the fill), `.lfpr`
(3 px, the quiet ink), `.dprog` (8 px in a well). `SIZES` and `TONES` are
the only choices; the line fills the width it is given. A change of
fraction slides the fill (the morph duration, a fade under reduced motion);
screen readers hear the label and the percentage.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import lumaui  # noqa: E402

__all__ = ["PROGRESS_SIZES", "PROGRESS_TONES", "ProgressLine"]

PROGRESS_SIZES = ("row", "tile", "meter", "hero", "well")
PROGRESS_TONES = ("accent", "good", "neutral", "danger", "chart")


def _clamp(value: float) -> float:
    return 0.0 if value != value else max(0.0, min(1.0, float(value)))  # NaN is nothing done


class ProgressLine(Gtk.Widget):
    """A thin line that fills from the start as something gets done."""

    __gtype_name__ = "LumaUIProgressLine"

    def __init__(self, fraction: float = 0.0, *, size: str = "row", tone: str = "accent",
                 label: str | None = None) -> None:
        if size not in PROGRESS_SIZES:
            raise ValueError(f"ProgressLine size must be one of {', '.join(PROGRESS_SIZES)}, not {size!r}")
        if tone not in PROGRESS_TONES:
            raise ValueError(f"ProgressLine tone must be one of {', '.join(PROGRESS_TONES)}, not {tone!r}")
        super().__init__(accessible_role=Gtk.AccessibleRole.PROGRESS_BAR, hexpand=True, valign=Gtk.Align.CENTER,
                         overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("lumaui-progress")
        self.add_css_class(size)
        self.size = size
        self.fill = Gtk.Box()
        self.fill.add_css_class("lumaui-progress-fill")
        self.fill.set_parent(self)
        self._tone = "accent"
        self.set_tone(tone)
        self._label = label
        self._fraction = _clamp(fraction)
        self._shown = self._fraction
        self._from = self._fraction
        self._tick = 0
        self._start = 0
        self._speak()

    # ── API ────────────────────────────────────────────────────────────────

    @property
    def fraction(self) -> float:
        return self._fraction

    def set_fraction(self, fraction: float) -> None:
        """Move to `fraction` (0 to 1), sliding the fill."""
        target = _clamp(fraction)
        if target == self._fraction:
            return
        self._fraction = target
        self._speak()
        duration = lumaui.duration("morph")
        if duration <= 0 or not self.get_mapped():
            self._shown = target
            self.queue_allocate()
            return
        self._from, self._start = self._shown, 0
        if not self._tick:
            self._tick = self.add_tick_callback(self._step)

    def set_tone(self, tone: str) -> None:
        if tone not in PROGRESS_TONES:
            raise ValueError(f"ProgressLine tone must be one of {', '.join(PROGRESS_TONES)}, not {tone!r}")
        self.remove_css_class(self._tone)
        self._tone = tone
        self.add_css_class(tone)

    def set_label(self, label: str | None) -> None:
        self._label = label
        self._speak()

    # ── drawing ────────────────────────────────────────────────────────────

    def _speak(self) -> None:
        percent = round(self._fraction * 100)
        self.update_property([Gtk.AccessibleProperty.VALUE_MIN, Gtk.AccessibleProperty.VALUE_MAX,
                              Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
                             [0.0, 100.0, float(percent), f"{percent}%"])
        if self._label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [self._label])

    def _step(self, _widget: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
        now = clock.get_frame_time()
        if not self._start:
            self._start = now
        duration = max(1, lumaui.duration("morph")) * 1000
        t = min(1.0, (now - self._start) / duration)
        eased = 1 - (1 - t) ** 3  # settles like the kit's ease curve
        self._shown = self._from + (self._fraction - self._from) * eased
        self.queue_allocate()
        if t >= 1.0:
            self._tick = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        # The track's height is its CSS min-height; its width is whatever it is given.
        return 0, 0, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.height = 0, 0, height
        rect.width = int(round(width * self._shown))
        if self.get_direction() == Gtk.TextDirection.RTL:
            rect.x = width - rect.width
        self.fill.set_child_visible(rect.width > 0)
        self.fill.size_allocate(rect, -1)

    def do_dispose(self) -> None:
        if self.fill.get_parent() is self:
            self.fill.unparent()
