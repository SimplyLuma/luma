# SPDX-License-Identifier: Apache-2.0
"""LumaUI media: AudioWaveform, the recording's shape as a seekable range (v70 `.mewave`, `.memini`).

The app supplies real peaks (0–1) and the duration; the kit never invents
them. It draws them in one snapshot as v70 does:

- `kind="well"` — `.mewave`: 160 round-ended bars 2 px apart in a recessed
  120 px well, the played part in the accent, a 2 px playhead with a soft
  ring, marks as small amber tabs above.
- `kind="deck"` — inside Memos' deck (`.medeck .mewave`, what
  `MediaTransport("waveform", waveform=…)` sets): 104 px, no well of its own,
  a 1.5 px playhead the full height, marks as 5 px dots above and a hairline
  rail below.
- `kind="clip"` — Messages' voice message `.wave` (VoiceClip uses it): 3 px
  bars 2 apart, 30 px tall, in the faint ink with the played part in the
  accent, as many as fit.
- `kind="mini"` — `.memini`: a 44 × 18 glance of every fourth peak for a row,
  in the muted ink or the accent when its row is chosen (`set_on`). Not
  interactive.

Live recording (`set_live(True)`, then `push_peak(v)` as audio arrives): 3 px
bars grow in from the end in the recording red, the newest last.

Interaction: a click seeks, a drag scrubs (Shift+drag selects a range, as
before: `selection-changed`). It is a slider to assistive technology
("Recording position", value text "0:12 of 1:03"); Left/Right move 5 s,
Page Up/Down 15 s, Home/End to the ends. A mark shows its time on hover.
Signals: `seek(seconds)` and `selection-changed(start, end)`.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GObject, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402
from . import media_style as style  # noqa: E402

__all__ = ["AudioWaveform", "WAVEFORM_KINDS", "resample_peaks", "mini_bars"]

W = tokens.MEDIA["waveform"]

#: The looks a waveform takes: Memos' standalone well, inside the deck, and a row's glance.
WAVEFORM_KINDS = ("well", "deck", "clip", "mini")


def _clean(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number)) if math.isfinite(number) else 0.0


def resample_peaks(peaks: Sequence[float], count: int) -> list[float]:
    """`count` bars from any number of peaks: each bar the loudest peak it covers (never averaged away)."""
    if count <= 0 or not peaks:
        return []
    n = len(peaks)
    if n == count:
        return list(peaks)
    bars = []
    for bar in range(count):
        begin = bar * n // count
        end = max(begin + 1, (bar + 1) * n // count)
        bars.append(max(peaks[begin:end]))
    return bars


def mini_bars(peaks: Sequence[float]) -> list[tuple[float, float, float]]:
    """The `.memini` rectangles in its 40 × 16 units: (x, y, height) for every fourth peak."""
    step = int(W["mini_step"])
    amp = W["mini_amp_units"]
    middle = W["mini_view_units"] / 2
    return [(float(i), middle - v * amp, v * 2 * amp) for i, v in enumerate(peaks[::step])]


class AudioWaveform(Gtk.Widget):
    """A recording's peaks as a seekable range: `AudioWaveform(kind="well")`, then `set_audio(peaks, duration)`."""

    __gtype_name__ = "LumaUIAudioWaveform"
    __gsignals__ = {
        "seek": (GObject.SignalFlags.RUN_LAST, None, (float,)),
        "selection-changed": (GObject.SignalFlags.RUN_LAST, None, (float, float)),
    }

    def __init__(self, kind: str = "well") -> None:
        if kind not in WAVEFORM_KINDS:
            raise ValueError(f"AudioWaveform kind must be one of {WAVEFORM_KINDS}, not {kind!r}")
        super().__init__(css_name="lumaui-waveform", hexpand=kind != "mini", focusable=kind != "mini",
                         overflow=Gtk.Overflow.VISIBLE)
        self.peaks: tuple[float, ...] = ()
        self.duration = 0.0
        self.position = 0.0
        self.selection: tuple[float, float] | None = None
        self.selection_mode = False
        self.marks: tuple[float, ...] = ()
        self.live = False
        self._on = False
        self.gap = float(W["gap"])
        self._bars_cache: tuple[int, list[float]] | None = None
        self._drag_start = 0.0
        self._selecting = False
        self.kind = ""
        self.set_kind(kind)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Recording position"])
        self.set_tooltip_text("")
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._tooltip)

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        self._drag = drag
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    # ── State ──
    def set_gap(self, gap: float | None) -> None:
        """The space between bars (the token's 2; Memos' deck on a phone draws them 1 apart)."""
        self.gap = float(W["gap"] if gap is None else gap)
        self._bars_cache = None
        self.queue_draw()

    def set_rail(self, visible: bool) -> None:
        """Show the deck's horizontal rail; phone transport decks omit it."""
        self._show_rail = bool(visible)
        self.queue_draw()

    def set_kind(self, kind: str) -> None:
        if kind not in WAVEFORM_KINDS:
            raise ValueError(f"AudioWaveform kind must be one of {WAVEFORM_KINDS}, not {kind!r}")
        if self.kind:
            self.remove_css_class(self.kind)
        self.kind = kind
        self.add_css_class(kind)
        interactive = kind != "mini"
        self.set_focusable(interactive)
        self.set_can_target(interactive)
        self.set_accessible_role(Gtk.AccessibleRole.SLIDER if interactive else Gtk.AccessibleRole.IMG)
        self.set_cursor_from_name("ew-resize" if interactive and not self.live else None)
        self._bars_cache = None
        self.queue_resize()

    def set_audio(self, peaks: Iterable[float], duration: float) -> None:
        self.peaks = tuple(_clean(p) for p in peaks)
        self.duration = _clean_duration(duration)
        self.selection = None
        self._bars_cache = None
        self.update_property([Gtk.AccessibleProperty.VALUE_MIN, Gtk.AccessibleProperty.VALUE_MAX],
                             [0.0, max(0.0, self.duration)])
        self.set_sensitive(self.duration > 0 or self.live or self.kind == "mini")
        self.set_position(0)

    def set_position(self, seconds: float) -> None:
        self.position = min(self.duration, max(0.0, _clean_duration(seconds)))
        self.update_property([Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT],
                             [self.position, f"{_clock(self.position)} of {_clock(self.duration)}"])
        self.queue_draw()

    def set_marks(self, seconds: Iterable[float]) -> None:
        """Moments the listener marked, in seconds (drawn above the bars, their time on hover)."""
        self.marks = tuple(sorted(_clean_duration(s) for s in seconds))
        self.queue_draw()

    def set_on(self, on: bool) -> None:
        """A mini waveform in a chosen row takes the accent."""
        self._on = bool(on)
        self.queue_draw()

    def set_live(self, live: bool) -> None:
        """Recording: 3 px bars grow in from the end as `push_peak` brings them."""
        self.live = bool(live)
        if self.live:
            self.peaks = ()
            self.set_sensitive(True)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Recording level" if self.live else "Recording position"])
        self.set_cursor_from_name(None if self.live else "ew-resize")
        self._bars_cache = None
        self.queue_resize()

    def set_selection_mode(self, enabled: bool) -> None:
        """Choose a clip range by dragging, or with arrow keys on either edge."""
        self.selection_mode = bool(enabled)
        self.selection = (0.0, self.duration) if enabled and self.duration else None
        self.set_cursor_from_name("crosshair" if enabled else "ew-resize")
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ["Trim selection; arrows move start, Shift plus arrows move end"
                              if enabled else "Recording position"])
        self.queue_draw()

    def push_peak(self, value: float) -> None:
        keep = int(W["live_keep"])
        self.peaks = (self.peaks + (_clean(value),))[-keep:]
        self.queue_draw()

    # ── Input ──
    def _time_at(self, x: float) -> float:
        width = max(1.0, self.get_width() - 2 * self._padding())
        return min(self.duration, max(0.0, (x - self._padding()) / width * self.duration))

    def _seek(self, seconds: float) -> None:
        self.set_position(seconds)
        self.emit("seek", self.position)

    def _pressed(self, _gesture: Gtk.GestureClick, _n: int, x: float, _y: float) -> None:
        if self.duration > 0 and not self.live:
            self.grab_focus()
            if not self.selection_mode and not (_gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK):
                self._seek(self._time_at(x))

    def _drag_begin(self, gesture: Gtk.GestureDrag, x: float, _y: float) -> None:
        self._drag_start = x
        self._selecting = self.selection_mode or bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)

    def _drag_update(self, _gesture: Gtk.GestureDrag, dx: float, _dy: float) -> None:
        if self.duration <= 0 or self.live:
            return
        if self._selecting:
            values = sorted(self._time_at(x) for x in (self._drag_start, self._drag_start + dx))
            self.selection = (values[0], values[1])
            self.queue_draw()
        else:
            self._seek(self._time_at(self._drag_start + dx))

    def _drag_end(self, _gesture: Gtk.GestureDrag, dx: float, _dy: float) -> None:
        if self._selecting and self.selection and abs(dx) >= 4:
            self.emit("selection-changed", *self.selection)
        self._selecting = False

    def _key(self, _controller, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        if self.duration <= 0 or self.live:
            return False
        if self.selection_mode and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            start, end = self.selection or (0.0, self.duration)
            delta = -float(W["seek_step"]) if keyval == Gdk.KEY_Left else float(W["seek_step"])
            if _state & Gdk.ModifierType.SHIFT_MASK:
                end = min(self.duration, max(start + .25, end + delta))
            else:
                start = max(0.0, min(end - .25, start + delta))
            self.selection = (start, end)
            self.emit("selection-changed", start, end)
            self.queue_draw()
            return True
        moves = {Gdk.KEY_Left: -W["seek_step"], Gdk.KEY_Right: W["seek_step"],
                 Gdk.KEY_Page_Down: -W["seek_page"], Gdk.KEY_Page_Up: W["seek_page"]}
        if keyval in moves:
            self._seek(self.position + moves[keyval])
            return True
        if keyval == Gdk.KEY_Home:
            self._seek(0.0)
            return True
        if keyval == Gdk.KEY_End:
            self._seek(self.duration)
            return True
        return False

    def _tooltip(self, _widget, x: int, _y: int, _keyboard: bool, tooltip: Gtk.Tooltip) -> bool:
        if not self.marks or self.duration <= 0:
            return False
        width = max(1.0, self.get_width() - 2 * self._padding())
        for mark in self.marks:
            if abs(self._padding() + mark / self.duration * width - x) <= W["mark"] / 2 + 2:
                tooltip.set_text(_clock(mark))
                return True
        return False

    # ── Layout ──
    def _padding(self) -> float:
        return float(W["padding_x"]) if self.kind == "well" else 0.0

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        if self.kind == "mini":
            side = W["mini_width"] if orientation == Gtk.Orientation.HORIZONTAL else W["mini_height"]
            return int(side), int(side), -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 48, 320 if self.kind != "clip" else 160, -1, -1
        if self.kind == "clip":
            height = tokens.MEDIA["voice"]["wave_height"]
            return int(height), int(height), -1, -1
        height = W["height"] if self.kind == "well" else (W["live_deck_height"] if self.live else W["deck_height"])
        return int(height), int(height), -1, -1

    # ── Drawing ──
    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        if self.kind == "mini":
            self._draw_mini(snapshot, width, height)
            return
        if self.kind == "well":
            self._draw_well(snapshot, width, height)
        if self.selection and self.duration:
            pad = self._padding()
            span = width - 2 * pad
            a, b = (pad + s / self.duration * span for s in self.selection)
            snapshot.append_color(style.colour(self, "luma_media_select", 0.18), style.rect(a, 0, b - a, height))
            if self.selection_mode:
                handle = style.colour(self, "luma_media_wave_played")
                for x in (a, b):
                    snapshot.append_color(handle, style.rect(max(0, min(width - 2, x - 1)), 0, 2, height))
        if self.live:
            self._draw_live(snapshot, width, height)
            return
        self._draw_bars(snapshot, width, height)
        if self.kind == "deck" and getattr(self, "_show_rail", True):
            rail = style.colour(self, "luma_ink", W["rail_alpha"])
            snapshot.append_color(rail, style.rect(0, height + W["rail_offset"], width, 1))
        if self.duration > 0 and self.kind != "clip":
            self._draw_head(snapshot, width, height)
        self._draw_marks(snapshot, width, height)

    def _draw_well(self, snapshot: Gtk.Snapshot, width: int, height: int) -> None:
        shape = style.rounded(style.rect(0, 0, width, height), W["radius"])
        snapshot.push_rounded_clip(shape)
        snapshot.append_color(style.colour(self, "luma_well"), style.rect(0, 0, width, height))
        snapshot.append_inset_shadow(shape, style.colour(self, "luma_well_shade"), 0, 1, 0, 2)
        snapshot.pop()
        snapshot.append_border(shape, [1] * 4, [style.colour(self, "luma_well_ring")] * 4)

    def _bars(self, count: int) -> list[float]:
        if self._bars_cache is None or self._bars_cache[0] != count:
            self._bars_cache = (count, resample_peaks(self.peaks, count))
        return self._bars_cache[1]

    def _draw_clip(self, snapshot: Gtk.Snapshot, width: int, height: int) -> None:
        voice = tokens.MEDIA["voice"]
        bar, gap = float(voice["wave_bar"]), float(voice["wave_gap"])
        count = max(1, int((width + gap) / (bar + gap)))
        bars = self._bars(count)
        played = self.position / self.duration if self.duration else 0.0
        ink, lit = style.colour(self, "luma_faint"), style.colour(self, "luma_media_wave_played")
        for index, value in enumerate(bars):
            h = max(2.0, value * height)
            box = style.rect(index * (bar + gap), (height - h) / 2, bar, h)
            snapshot.push_rounded_clip(style.rounded(box, float(W["bar_radius"])))
            snapshot.append_color(lit if index / count < played else ink, box)
            snapshot.pop()

    def _draw_bars(self, snapshot: Gtk.Snapshot, width: int, height: int) -> None:
        if not self.peaks:
            return
        if self.kind == "clip":
            self._draw_clip(snapshot, width, height)
            return
        pad, gap = self._padding(), self.gap
        span = width - 2 * pad
        count = min(int(W["bars"]), max(1, int((span + gap) / (W["bar_min"] + gap))))
        bars = self._bars(count)
        bar = max(float(W["bar_min"]), (span - gap * (count - 1)) / count)
        played = self.position / self.duration if self.duration else 0.0
        deck = self.kind == "deck"
        ink = style.colour(self, "luma_ink", W["deck_ink_alpha"] if deck else W["ink_alpha"])
        lit = style.colour(self, "luma_media_wave_deck_played" if deck else "luma_media_wave_played")
        radius = min(float(W["bar_radius"]), bar / 2)
        for index, value in enumerate(bars):
            h = max(1.0, value * height)
            x = pad + index * (bar + gap)
            box = style.rect(x, (height - h) / 2, bar, h)
            colour = lit if index / count < played else ink
            snapshot.push_rounded_clip(style.rounded(box, radius))
            snapshot.append_color(colour, box)
            snapshot.pop()

    def _draw_live(self, snapshot: Gtk.Snapshot, width: int, height: int) -> None:
        bar, gap = float(W["live_bar"]), self.gap
        pad = self._padding()
        fits = int((width - 2 * pad + gap) / (bar + gap))
        colour = style.colour(self, "luma_media_recording",
                              W["deck_live_alpha"] if self.kind == "deck" else W["live_alpha"])
        peaks = self.peaks[-fits:] if fits > 0 else ()
        x = width - pad - bar
        for value in reversed(peaks):
            h = max(1.0, value * height)
            box = style.rect(x, (height - h) / 2, bar, h)
            snapshot.push_rounded_clip(style.rounded(box, min(float(W["bar_radius"]), bar / 2)))
            snapshot.append_color(colour, box)
            snapshot.pop()
            x -= bar + gap

    def _draw_head(self, snapshot: Gtk.Snapshot, width: int, height: int) -> None:
        pad = self._padding()
        x = pad + self.position / self.duration * (width - 2 * pad)
        if self.kind == "deck":
            line = W["deck_head_width"]
            snapshot.append_color(style.colour(self, "luma_ink", W["deck_head_alpha"]),
                                  style.rect(x - line / 2, 0, line, height))
            return
        line, inset, ring = W["head_width"], W["head_inset"], W["head_ring"]
        box = style.rect(x - line / 2, inset, line, height - 2 * inset)
        shape = style.rounded(box, line / 2)
        snapshot.append_outset_shadow(shape, style.colour(self, "luma_ink", W["head_ring_alpha"]), 0, 0, ring, 0)
        snapshot.push_rounded_clip(shape)
        snapshot.append_color(style.colour(self, "luma_ink"), box)
        snapshot.pop()

    def _draw_marks(self, snapshot: Gtk.Snapshot, width: int, _height: int) -> None:
        if not self.marks or self.duration <= 0:
            return
        pad = self._padding()
        colour = style.colour(self, "luma_media_mark")
        for mark in self.marks:
            x = pad + mark / self.duration * (width - 2 * pad)
            if self.kind == "deck":
                size = W["deck_mark"]
                box = style.rect(x - size / 2, -W["deck_mark_top"], size, size)
                snapshot.push_rounded_clip(style.rounded(box, size / 2))
                snapshot.append_color(style.colour(self, "luma_media_mark", 0.9), box)
                snapshot.pop()
            else:
                size = W["mark"]
                box = style.rect(x - size / 2, -W["mark_top"], size, size)
                top, bottom = W["mark_radius_top"], W["mark_radius_bottom"]
                shape = style.rounded(box, top, top, bottom, bottom)
                snapshot.append_outset_shadow(shape, style.colour(self, "luma_media_shadow", 0.4), 0, 1, 0, 3)
                snapshot.push_rounded_clip(shape)
                snapshot.append_color(colour, box)
                snapshot.pop()

    def _draw_mini(self, snapshot: Gtk.Snapshot, width: int, height: int) -> None:
        colour = style.colour(self, "luma_media_wave_mini_on" if self._on else "luma_muted")
        sx, sy = width / W["mini_units"], height / W["mini_view_units"]
        bar = W["mini_bar_units"] * sx
        for x, y, h in mini_bars(self.peaks):
            if x >= W["mini_units"]:
                break
            snapshot.append_color(colour, style.rect(x * sx, y * sy, bar, max(0.5, h * sy)))


def _clean_duration(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, number) if math.isfinite(number) else 0.0


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"
