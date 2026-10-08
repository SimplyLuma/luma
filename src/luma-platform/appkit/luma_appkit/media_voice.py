# SPDX-License-Identifier: Apache-2.0
"""LumaUI media: VoiceClip (backlog MD2), a short recording you play in place.

A voice message (Messages), a voicemail (Phone), a memo in a list (Memos):
a round play key, the recording's shape or a thin progress bar, and its time.

    clip = VoiceClip(12.4, peaks=message.peaks, on_toggle=player.toggle, on_seek=player.seek)   # Messages
    clip = VoiceClip(31, on_toggle=…)                                                        # Phone voicemail
    player.connect("position", lambda s: clip.set_position(s))

- With `peaks`: v70 `.mvoice`, a 32 px soft key, the peaks as 3 px bars
  (AudioWaveform kind "clip") with the played part in the accent, and the time.
- Without: v70 `.pnvm`, a 40 px soft key, a 4 px progress bar, and the time.

The time is the length at rest and the time left once it has started
("0:12", then "0:07"). Clicking the shape or the bar seeks; the key reads
"Play voice message, 0:12" or "Pause". Space or Enter on the key plays and
pauses; the bar is a slider (arrows move 1 s, Home/End).
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402
from . import media_style as style  # noqa: E402

__all__ = ["VoiceClip", "clip_time"]

V = tokens.MEDIA["voice"]


def clip_time(position: float, duration: float) -> str:
    """The clip's time: its length at rest, the time left once it has started ("0:07")."""
    duration = max(0.0, float(duration))
    position = min(duration, max(0.0, float(position)))
    seconds = duration - position if position > 0 else duration
    total = int(math.ceil(seconds - 1e-6)) if position > 0 else int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


class _Progress(Gtk.Widget):
    """The voicemail's 4 px bar, a slider to assistive technology."""

    __gtype_name__ = "LumaUIVoiceProgress"

    def __init__(self, clip: "VoiceClip") -> None:
        super().__init__(css_name="lumaui-voice-bar", hexpand=True, valign=Gtk.Align.CENTER, focusable=True)
        self._clip = clip
        self.set_accessible_role(Gtk.AccessibleRole.SLIDER)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Position"])
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", lambda _g, _n, x, _y: clip._seek_fraction(x / max(1, self.get_width())))
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def _key(self, _controller, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        clip = self._clip
        moves = {Gdk.KEY_Left: -1.0, Gdk.KEY_Right: 1.0}
        if keyval in moves:
            clip._seek(clip.position + moves[keyval])
            return True
        if keyval in (Gdk.KEY_Home, Gdk.KEY_End):
            clip._seek(0.0 if keyval == Gdk.KEY_Home else clip.duration)
            return True
        return False

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 40, 160, -1, -1
        return int(V["bar"]), int(V["bar"]), -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        box = style.rect(0, 0, width, height)
        snapshot.push_rounded_clip(style.rounded(box, V["bar_radius"]))
        snapshot.append_color(style.colour(self, "luma_control_fill"), box)
        fraction = self._clip.position / self._clip.duration if self._clip.duration else 0.0
        if self.get_direction() == Gtk.TextDirection.RTL:
            snapshot.append_color(style.colour(self, "luma_media_wave_played"),
                                  style.rect(width * (1 - fraction), 0, width * fraction, height))
        else:
            snapshot.append_color(style.colour(self, "luma_media_wave_played"), style.rect(0, 0, width * fraction, height))
        snapshot.pop()


class VoiceClip(Gtk.Box):
    """`VoiceClip(duration, peaks=None, position=0, playing=False, on_toggle=, on_seek=)`."""

    __gtype_name__ = "LumaUIVoiceClip"

    def __init__(self, duration: float, *, peaks: Sequence[float] | None = None, position: float = 0.0,
                 playing: bool = False, label: str = "Voice message", time_mode: str = "remaining",
                 on_toggle: Callable[[bool], None] | None = None,
                 on_seek: Callable[[float], None] | None = None) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.GROUP, valign=Gtk.Align.CENTER)
        self.add_css_class("lumaui-voice-clip")
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if time_mode not in ("remaining", "duration"):
            raise ValueError("time_mode is remaining or duration")
        self.time_mode = time_mode
        self._label = label
        self.duration = max(0.0, float(duration)) if math.isfinite(float(duration)) else 0.0
        self.position = 0.0
        self.playing = False
        self.on_toggle, self.on_seek = on_toggle, on_seek
        from .media_transport import MediaGlyph
        from .waveform import AudioWaveform

        waveform = peaks is not None
        self.add_css_class("wave" if waveform else "bar")
        self._glyph = MediaGlyph("play", V["key_icon"] if waveform else V["big_key_icon"], filled=True)
        self.key = Gtk.Button(child=self._glyph, valign=Gtk.Align.CENTER)
        self.key.add_css_class("lumaui-voice-key")
        self.key.connect("clicked", lambda _b: self.toggle())
        self.append(self.key)
        if waveform:
            self.shape = AudioWaveform("clip")
            self.shape.set_audio(peaks or (), self.duration)
            self.shape.set_valign(Gtk.Align.CENTER)
            self.shape.connect("seek", lambda _w, seconds: self._seek(seconds))
        else:
            self.shape = _Progress(self)
        self.append(self.shape)
        self.time = Gtk.Label(xalign=1)
        self.time.add_css_class("lumaui-voice-time")
        self.append(self.time)
        self.set_playing(playing)
        self.set_position(position)

    def toggle(self) -> None:
        self.set_playing(not self.playing)
        if self.on_toggle is not None:
            self.on_toggle(self.playing)

    def set_playing(self, playing: bool) -> None:
        self.playing = bool(playing)
        self._glyph.set_name_("pause" if self.playing else "play")
        text = "Pause" if self.playing else f"Play {self._label.lower()}, {clip_time(0, self.duration)}"
        self.key.update_property([Gtk.AccessibleProperty.LABEL], [text])
        self.key.set_tooltip_text("Pause" if self.playing else "Play")

    def set_position(self, seconds: float) -> None:
        seconds = float(seconds) if math.isfinite(float(seconds)) else 0.0
        self.position = min(self.duration, max(0.0, seconds))
        if hasattr(self.shape, "set_position"):
            self.shape.set_position(self.position)
        else:
            self.shape.queue_draw()
            self.shape.update_property([Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_MAX,
                                        Gtk.AccessibleProperty.VALUE_TEXT],
                                       [self.position, self.duration, f"{clip_time(0, self.position)} of "
                                                                      f"{clip_time(0, self.duration)}"])
        self.time.set_label(clip_time(0 if self.time_mode == "duration" else self.position, self.duration))

    def _seek_fraction(self, fraction: float) -> None:
        self._seek(min(1.0, max(0.0, fraction)) * self.duration)

    def _seek(self, seconds: float) -> None:
        self.set_position(seconds)
        if self.on_seek is not None:
            self.on_seek(self.position)
