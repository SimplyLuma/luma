# SPDX-License-Identifier: Apache-2.0
"""LumaUI media: MediaTransport (backlog MD1), one transport for Tide, Reel, Session and Memos.

Play, seek, time, step, loop, shuffle, repeat, volume and speed in one state
model. The app says where playback is and what the listener asked for; the
kit owns the keys, the scrub, the readouts, their order and their look.

    transport = MediaTransport("deck", duration=184, shuffle=False, repeat=False,
                               on_play=player.set_playing, on_seek=player.seek, on_step=queue.step)
    deck.append(transport)                       # Tide: the deck's centre column
    deck_end.append(transport.volume_control)    # …and its volume, at the deck's end

    transport = MediaTransport("attached", duration=clip.length, fps=24, loop=False,
                               on_play=…, on_seek=…, on_step=…, on_fullscreen=…)   # Reel, under the picture
    transport = MediaTransport("lcd", fps=None, lcd=[(("12", "3", "1"), "Bar"), ("0:21.4", "Time")],
                               readouts=[("112", "BPM", edit_tempo)], loop=False, on_record=…)  # Session's bar
    transport = MediaTransport("waveform", duration=memo.length, speed=1.0, waveform=AudioWaveform(),
                               on_play=…, on_seek=…, on_skip=…, on_speed=…)        # Memos' deck
    player.connect("position", lambda s: transport.set_position(s))

Variants (v70):
- `deck` — Tide `#t-bot .dctlw`: shuffle · previous · play · next · repeat,
  6 apart, the 44 px play key between them; under them the position, a 5 px
  scrub and the time left ("−2:41"), 11 px tabular. `volume_control` is the
  deck end's speaker and 76 px range, for the app to place. The shouldered
  deck, sleeve and song text are Tide's own (triage: app-only).
- `attached` — Reel `.rlctl`: 58 px under the picture, the LCD (Timecode with
  its frames dimmed, Length) at the start, start · frame back · play · frame
  forward · end in the middle, loop and full screen at the end.
- `lcd` — Session's bar: back to start · play · record, the LCD (the app's
  readouts, e.g. Bar "12.3.1" with its dots dimmed, Time), then readout keys
  (BPM, time signature, key) and loop.
- `waveform` — Memos `.medeck`: the recessed deck with the app's waveform
  (an AudioWaveform) on top; the time ("0:12 / 1:03"), back 15 · play · on
  15, and the speed ("1×", a menu) beneath it.

Every change is reported, never assumed: a key calls its callback and shows
the new state at once (a state changes on press); the app calls the setters
as the real player moves. While the scrub is held the app's position updates
wait, so the thumb never fights the finger.

Keyboard: every key is a button (Tab, Enter, Space); the scrub and volume are
ranges (arrows, Page Up/Down, Home/End). `attach_shortcuts(window)` adds the
player's own keys: Space plays and pauses, Left/Right step (a frame, a track or
15 s), Home and End go to the start and end. Each key reads its action
("Play", "Pause", "Back one frame"); toggles read pressed or not.
"""
from __future__ import annotations

import math
import time
from typing import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from . import lumaui_tokens as tokens  # noqa: E402
from . import media_style as style  # noqa: E402

__all__ = ["MediaReadout", "MediaTransport", "TRANSPORT_VARIANTS", "SPEEDS", "clock_text", "remaining_text", "timecode_parts",
           "MediaGlyph"]

TR = tokens.MEDIA["transport"]

#: Transport variants, in v70's words: Tide's deck, Reel's attached bar, Session's LCD bar, Memos' deck.
TRANSPORT_VARIANTS = ("deck", "attached", "lcd", "waveform", "now")

#: Playback speeds the waveform deck offers, slowest first.
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

MINUS = "−"


# ── Pure formatting ────────────────────────────────────────────────────────

def clock_text(seconds: float) -> str:
    """"m:ss", or "h:mm:ss" past an hour; never negative."""
    total = max(0, int(math.floor(float(seconds) + 1e-6))) if math.isfinite(float(seconds)) else 0
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def remaining_text(position: float, duration: float) -> str:
    """The time left, as v70 writes it: "−2:41" (a real minus sign)."""
    left = max(0.0, float(duration) - float(position))
    return MINUS + clock_text(math.ceil(left - 1e-6))


def timecode_parts(seconds: float, fps: float | None) -> tuple[str, str]:
    """("MM:SS", ":FF"): the timecode and its frames, which the LCD dims (v70 `rlTC`)."""
    seconds = max(0.0, float(seconds)) if math.isfinite(float(seconds)) else 0.0
    if not fps:
        return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}", ""
    frames = int(round(seconds * fps))
    whole = int(frames // fps)
    return f"{whole // 60:02d}:{whole % 60:02d}", f":{int(frames % fps):02d}"


def speed_text(rate: float) -> str:
    return f"{rate:g}×"


# ── A glyph that can be filled ─────────────────────────────────────────────

class MediaGlyph(Gtk.Widget):
    """A Lucide glyph in the widget's own ink, stroked like the theme, or filled (play, skip, heart).

    The glyph is the theme's icon (a `Gtk.Image`, so it is named and measured like
    every other icon); a filled glyph also has its shape painted underneath in the
    same ink, which is how v70 fills its stroke icons (`fill: currentColor`).
    """

    __gtype_name__ = "LumaUIMediaGlyph"

    def __init__(self, name: str, size: float = 16, *, filled: bool = False) -> None:
        super().__init__(css_name="lumaui-media-glyph", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         can_target=False)
        from .icons import icon_name

        self._name, self._size, self._filled = name, float(size), filled
        self.image = Gtk.Image.new_from_icon_name(icon_name(name))
        self.image.set_pixel_size(int(round(size)))
        self.image.set_parent(self)
        self.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)

    @property
    def name(self) -> str:
        return self._name

    def set_name_(self, name: str) -> None:
        if name != self._name:
            from .icons import icon_name

            self._name = name
            self.image.set_from_icon_name(icon_name(name))
            self.queue_draw()

    def set_size(self, size: float) -> None:
        self._size = float(size)
        self.image.set_pixel_size(int(round(size)))
        self.queue_resize()

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        side = int(math.ceil(self._size))
        return side, side, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self.image.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._filled:
            path = style.glyph_path(self._name)
            if path is not None:
                from gi.repository import Gsk

                snapshot.save()
                snapshot.scale(self._size / 24.0, self._size / 24.0)
                snapshot.append_fill(path, Gsk.FillRule.WINDING, self.get_color())
                snapshot.restore()
        self.snapshot_child(self.image, snapshot)

    def do_dispose(self) -> None:
        if self.image.get_parent() is self:
            self.image.unparent()


# ── Keys ───────────────────────────────────────────────────────────────────

#: What each key says, per variant, in v70's words (the title carries the shortcut where v70 shows one).
TITLES = {
    "attached": {"start": "Start · Home", "back": "Back one frame · ←", "play": "Play · Space", "pause": "Pause · Space",
                 "forward": "Forward one frame · →", "end": "End · End", "loop": "Loop", "fullscreen": "Full screen"},
    "lcd": {"start": "Back to start · ⏎", "play": "Play · Space", "pause": "Pause · Space", "record": "Record · R",
            "loop": "Loop · C"},
    "waveform": {"back": "Back 15 seconds", "play": "Play (Space)", "pause": "Pause (Space)",
                 "forward": "Forward 15 seconds"},
}


def _key(icon: str, label: str, callback: Callable[[], None], *, filled: bool = False, size: float | None = None,
         css: Iterable[str] = ()) -> Gtk.Button:
    glyph = MediaGlyph(icon, size or TR["icon"], filled=filled)
    button = Gtk.Button(child=glyph, tooltip_text=label, valign=Gtk.Align.CENTER)
    button.add_css_class("lumaui-media-key")
    for name in css:
        button.add_css_class(name)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    button.connect("clicked", lambda _b: callback())
    button.glyph = glyph
    return button


def _toggle(button: Gtk.Button, on: bool) -> None:
    (button.add_css_class if on else button.remove_css_class)("on")
    button.update_state([Gtk.AccessibleState.PRESSED], [int(bool(on))])


def _label(css: str, text: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class(css)
    return label


# ── The transport ──────────────────────────────────────────────────────────

class MediaReadout(Gtk.Button):
    """Live value over a caption, matching the transport's tempo/signature keys."""

    __gtype_name__ = "LumaUIMediaReadout"

    def __init__(self, value: str, caption: str, *, chip: bool = False, on_activate=None):
        super().__init__()
        self.add_css_class("lumaui-media-readout")
        self._caption = str(caption)
        self.set_chip(chip)
        self._value_label = _label("value", "")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._value_label)
        box.append(_label("caption", self._caption))
        self.set_child(box)
        self.set_value(value)
        if on_activate is not None:
            self.connect("clicked", lambda _button: on_activate())

    def set_chip(self, chip: bool) -> None:
        if chip:
            self.add_css_class("chip")
        else:
            self.remove_css_class("chip")

    def set_value(self, value: str) -> None:
        self._value_label.set_text(str(value))
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{self._caption}: {value}"])


class MediaTransport(Gtk.Box):
    """One transport, four shapes: `MediaTransport("deck" | "attached" | "lcd" | "waveform", …)`."""

    __gtype_name__ = "LumaUIMediaTransport"

    def __init__(self, variant: str = "deck", *, duration: float = 0.0, position: float = 0.0,
                 playing: bool = False, fps: float | None = None, loop: bool | None = None,
                 shuffle: bool | None = None, repeat: bool | None = None, volume: float | None = None,
                 speed: float | None = None, recording: bool | None = None,
                 lcd: Sequence[tuple] | None = None, readouts: Sequence[tuple] = (),
                 waveform: Gtk.Widget | None = None, label: str = "Playback",
                 speeds: Sequence[float] = SPEEDS, speed_extras: Callable[[], Sequence] | None = None,
                 on_play: Callable[[bool], None] | None = None,
                 on_seek: Callable[[float], None] | None = None,
                 on_step: Callable[[int], None] | None = None,
                 on_skip: Callable[[float], None] | None = None,
                 on_loop: Callable[[bool], None] | None = None,
                 on_shuffle: Callable[[bool], None] | None = None,
                 on_repeat: Callable[[bool], None] | None = None,
                 on_volume: Callable[[float], None] | None = None,
                 on_speed: Callable[[float], None] | None = None,
                 on_record: Callable[[bool], None] | None = None,
                 on_fullscreen: Callable[[], None] | None = None) -> None:
        if variant not in TRANSPORT_VARIANTS:
            raise ValueError(f"MediaTransport variant must be one of {TRANSPORT_VARIANTS}, not {variant!r}")
        vertical = variant in ("deck", "waveform", "now")
        super().__init__(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        self.add_css_class("lumaui-media-transport")
        self.add_css_class(variant)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.variant = variant
        self._duration = max(0.0, float(duration))
        self._position = 0.0
        self._playing = bool(playing)
        self._fps = fps
        self._loop, self._shuffle, self._repeat = loop, shuffle, repeat
        self._volume = volume
        self._speed = speed
        self._recording = recording
        # The speed menu: the rates it offers and the app's own rows under a hairline (Memos' "Skip silences").
        self.speeds, self.speed_extras = tuple(speeds), speed_extras
        self._held_until = 0.0
        self._scrubbing = False
        self._updating = False
        self.on_play, self.on_seek, self.on_step, self.on_skip = on_play, on_seek, on_step, on_skip
        self.on_loop, self.on_shuffle, self.on_repeat = on_loop, on_shuffle, on_repeat
        self.on_volume, self.on_speed, self.on_record, self.on_fullscreen = on_volume, on_speed, on_record, on_fullscreen

        self.play_key = _key("play", TITLES.get(variant, {}).get("play", "Play"), self.toggle_play, filled=True,
                             size=TR["memo_play_icon"] if variant == "waveform" else TR["play_icon"],
                             css=("lumaui-media-play",))
        self.scrub: Gtk.Scale | None = None
        self.volume_control: Gtk.Box | None = None
        self._keys: dict[str, Gtk.Button] = {}
        self._lcd_box: Gtk.Box | None = None

        build = {"deck": self._build_deck, "attached": self._build_attached, "lcd": self._build_lcd,
                 "waveform": self._build_waveform, "now": self._build_now}[variant]
        build(lcd=lcd, readouts=readouts, waveform=waveform)
        if volume is not None:
            self._build_volume()
        self.set_playing(playing)
        self.set_position(position)

    # ── Building ──
    def _keyrow(self, css: str) -> Gtk.Box:
        row = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        row.add_css_class(css)
        return row

    def _title(self, key: str, fallback: str) -> str:
        return TITLES.get(self.variant, {}).get(key, fallback)

    def _add_key(self, row: Gtk.Box, key: str, icon: str, label: str, callback: Callable[[], None], **kwargs) -> None:
        if self.variant == "attached":
            kwargs.setdefault("size", TR["attached_icon"])
        button = _key(icon, self._title(key, label), callback, **kwargs)
        self._keys[key] = button
        row.append(button)

    def _build_deck(self, **_kw) -> None:
        keys = self._keyrow("lumaui-media-keys")
        if self._shuffle is not None:
            self._add_key(keys, "shuffle", "shuffle", "Shuffle", self._flip_shuffle, css=("toggle",))
        self._add_key(keys, "previous", "skip-back", "Previous", lambda: self._step(-1), filled=True)
        keys.append(self.play_key)
        self._add_key(keys, "next", "skip-forward", "Next", lambda: self._step(1), filled=True)
        if self._repeat is not None:
            self._add_key(keys, "repeat", "repeat", "Repeat", self._flip_repeat, css=("toggle",))
        self.append(keys)
        scrub = Gtk.Box()
        scrub.add_css_class("lumaui-media-scrub")
        self._elapsed = _label("lumaui-media-time")
        self._elapsed.set_xalign(0)
        self._left = _label("lumaui-media-time")
        self._left.set_xalign(1)
        self.scrub = self._range("Position", self._seek_to)
        self.scrub.set_hexpand(True)
        scrub.append(self._elapsed)
        scrub.append(self.scrub)
        scrub.append(self._left)
        self.append(scrub)
        self._sync_toggles()

    def _build_now(self, **_kw) -> None:
        """A phone's Now Playing (v71 `.tnowph`): the scrub across, the elapsed time and the time left under
        its ends, shuffle · previous · play · next · repeat spread across, then (given `volume=`) the volume."""
        self.scrub = self._range("Position", self._seek_to)
        self.scrub.set_hexpand(True)
        self.append(self.scrub)
        times = Gtk.Box()
        times.add_css_class("lumaui-media-scrub-times")
        self._elapsed = _label("lumaui-media-time")
        self._elapsed.set_xalign(0)
        self._elapsed.set_hexpand(True)
        self._left = _label("lumaui-media-time")
        self._left.set_xalign(1)
        times.append(self._elapsed)
        times.append(self._left)
        self.append(times)
        keys = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER)  # space-between: a spacer after each key but the last
        keys.add_css_class("lumaui-media-keys")
        self.play_key.glyph.set_size(32)
        if self._shuffle is not None:
            self._add_key(keys, "shuffle", "shuffle", "Shuffle", self._flip_shuffle, css=("toggle",), size=22)
        self._add_key(keys, "previous", "skip-back", "Previous", lambda: self._step(-1), filled=True, size=32)
        keys.append(self.play_key)
        self._add_key(keys, "next", "skip-forward", "Next", lambda: self._step(1), filled=True, size=32)
        if self._repeat is not None:
            self._add_key(keys, "repeat", "repeat", "Repeat", self._flip_repeat, css=("toggle",), size=22)
        self.append(self._spread(keys))
        self._sync_toggles()

    @staticmethod
    def _spread(row: Gtk.Box) -> Gtk.Box:
        """`row` with an expanding spacer between every pair of its children (CSS space-between)."""
        children = []
        child = row.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        for child in children[:-1]:
            row.insert_child_after(Gtk.Box(hexpand=True), child)
        return row

    def _build_attached(self, **_kw) -> None:
        center = Gtk.CenterBox(hexpand=True)
        center.set_start_widget(self._build_lcd_box([None, None]))
        keys = self._keyrow("lumaui-media-keys")
        keys.add_css_class("middle")
        self._add_key(keys, "start", "skip-back", "Start", lambda: self._seek_to(0.0, user=True))
        self._add_key(keys, "back", "step-back", "Back one frame", lambda: self._step(-1))
        keys.append(self.play_key)
        self._add_key(keys, "forward", "step-forward", "Forward one frame", lambda: self._step(1))
        self._add_key(keys, "end", "skip-forward", "End", lambda: self._seek_to(self._duration, user=True))
        center.set_center_widget(keys)
        end = Gtk.Box(halign=Gtk.Align.END)
        end.add_css_class("lumaui-media-keys")
        self._phone_hidden = [end]
        if self._loop is not None:
            self._add_key(end, "loop", "repeat", "Loop", self._flip_loop, css=("toggle",))
        if self.on_fullscreen is not None:
            self._add_key(end, "fullscreen", "maximize-2", "Full screen", lambda: self.on_fullscreen())
        center.set_end_widget(end)
        self.append(center)
        self._phone_hidden += [self._keys["back"], self._keys["forward"]]
        self.connect("notify::root", self._watch_root)
        self._sync_toggles()

    def _build_lcd(self, lcd: Sequence[tuple] | None = None, readouts: Sequence[tuple] = (), **_kw) -> None:
        keys = Gtk.Box()
        keys.add_css_class("lumaui-media-keys")
        self._add_key(keys, "start", "skip-back", "Back to start", lambda: self._seek_to(0.0, user=True))
        keys.append(self.play_key)
        if self._recording is not None:
            record = Gtk.Button(tooltip_text=self._title("record", "Record"))
            record.add_css_class("lumaui-media-record")
            dot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
            dot.add_css_class("dot")
            record.set_child(dot)
            record.update_property([Gtk.AccessibleProperty.LABEL], [self._title("record", "Record")])
            record.connect("clicked", lambda _b: self._flip_record())
            self._keys["record"] = record
            keys.append(record)
        self.append(keys)
        self.append(self._build_lcd_box(list(lcd or [("", "Time")])))
        for value, caption, *rest in readouts:
            callback = rest[0] if rest else None
            button = MediaReadout(str(value), caption)
            if callback is not None:
                button.connect("clicked", lambda _b, fn=callback: fn())
            else:
                button.set_can_target(False)
            self.append(button)
        if self._loop is not None:
            separator = Gtk.Box(valign=Gtk.Align.CENTER)
            separator.add_css_class("lumaui-media-separator")
            self.append(separator)
            end = Gtk.Box()
            end.add_css_class("lumaui-media-keys")
            self._add_key(end, "loop", "repeat", "Loop", self._flip_loop, css=("toggle",))
            self.append(end)
        self._sync_toggles()

    def _build_waveform(self, waveform: Gtk.Widget | None = None, **_kw) -> None:
        self.waveform = waveform
        if waveform is not None:
            if hasattr(waveform, "set_kind"):
                waveform.set_kind("deck")
            if hasattr(waveform, "connect") and GObject.signal_lookup("seek", type(waveform)):
                waveform.connect("seek", lambda _w, seconds: self._seek_to(seconds, user=True))
            self.append(waveform)
        controls = Gtk.CenterBox()
        controls.add_css_class("lumaui-media-controls")
        self._column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)  # keys over (time · speed) on a phone
        self._column.add_css_class("lumaui-media-column")
        self._controls = controls
        self._elapsed = _label("lumaui-media-time")
        self._total = _label("lumaui-media-total")
        time_box = Gtk.Box(valign=Gtk.Align.CENTER)
        time_box.append(self._elapsed)
        time_box.append(self._total)
        controls.set_start_widget(time_box)
        keys = self._keyrow("lumaui-media-keys")
        self._add_key(keys, "back", "rotate-ccw", "Back 15 seconds", lambda: self._skip(-15))
        keys.append(self.play_key)
        self._add_key(keys, "forward", "rotate-cw", "Forward 15 seconds", lambda: self._skip(15))
        controls.set_center_widget(keys)
        if self._speed is not None:
            speed = Gtk.Button(halign=Gtk.Align.END, valign=Gtk.Align.CENTER, tooltip_text="Speed")
            speed.add_css_class("lumaui-media-speed")
            row = Gtk.Box()
            self._speed_label = Gtk.Label()
            row.append(self._speed_label)
            chevron = MediaGlyph("chevron-down", TR["speed_icon"])
            chevron.add_css_class("chevron")
            row.append(chevron)
            speed.set_child(row)
            speed.connect("clicked", self._speed_menu)
            self._keys["speed"] = speed
            controls.set_end_widget(speed)
            self.set_speed(self._speed)
        self._column.append(controls)
        self._keys_row = keys
        self.append(self._column)
        self.connect("notify::root", self._watch_root)

    def _build_lcd_box(self, readouts: list) -> Gtk.Box:
        box = Gtk.Box(valign=Gtk.Align.CENTER)
        box.add_css_class("lumaui-media-lcd")
        self._lcd_box = box
        self._lcd_cells: list[tuple[Gtk.Box, Gtk.Label]] = []
        captions = ("Timecode", "Length") if self.variant == "attached" else [r[1] for r in readouts]
        for index, caption in enumerate(captions):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
            cell.add_css_class("cell")
            value = Gtk.Box(halign=Gtk.Align.CENTER)
            value.add_css_class("value")
            cell.append(value)
            small = _label("caption", caption)
            cell.append(small)
            box.append(cell)
            self._lcd_cells.append((value, small))
        if self.variant == "lcd":
            self.set_lcd(readouts)
        return box

    def _build_volume(self) -> None:
        box = Gtk.Box(valign=Gtk.Align.CENTER)
        box.add_css_class("lumaui-media-volume")
        now = self.variant == "now"  # v71 .tnvol: volume-1, the range, volume-2, 18 each
        box.append(MediaGlyph("volume-1" if now else "volume-2", 18 if now else TR["icon"]))
        self._volume_range = self._range("Volume", self._volume_to, upper=1.0)
        self._volume_range.set_hexpand(now)
        box.append(self._volume_range)
        if now:
            box.append(MediaGlyph("volume-2", 18))
            self.append(box)
        self.volume_control = box
        self.set_volume(self._volume)

    def set_volume_compact(self, compact: bool) -> None:
        """Fold the volume range while keeping its glyph and current value.

        Deck hosts use this when their end controls have less room. The default
        remains the full range; expanding restores the same live adjustment.
        """
        if self.volume_control is not None:
            self._volume_range.set_visible(not compact)

    def _range(self, label: str, callback: Callable[..., None], upper: float | None = None) -> Gtk.Scale:
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        scale.set_draw_value(False)
        scale.add_css_class("lumaui-media-range")
        scale.update_property([Gtk.AccessibleProperty.LABEL], [label])
        if upper is not None:
            scale.set_range(0, upper)
            scale.set_increments(0.05, 0.1)

        def changed(_scale: Gtk.Scale, _scroll: Gtk.ScrollType, value: float) -> bool:
            if not self._updating:
                callback(min(max(value, 0.0), _scale.get_adjustment().get_upper()), user=True)
            return False

        scale.connect("change-value", changed)
        press = Gtk.GestureClick()
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda *_: setattr(self, "_scrubbing", True))
        press.connect("stopped", lambda *_: self._release())
        press.connect("released", lambda *_: self._release())
        scale.add_controller(press)
        return scale

    def _release(self) -> None:
        self._scrubbing = False
        self._held_until = time.monotonic() + 0.25

    # ── Phone width ──
    def _watch_root(self, *_args: object) -> None:
        """Narrow, Reel's bar keeps the LCD, start, play and end (v70 line 3642)."""
        root = self.get_root()
        if root is None:
            return
        if getattr(self, "_phone_tick", 0) == 0:
            # A window's width has no signal of its own; a per-frame integer compare is cheap.
            def tick(_widget: Gtk.Widget, _clock: object) -> bool:
                current = self.get_root()
                width = (current.get_width() if current is not None else 0, self._available_width())
                if width != getattr(self, "_seen_width", -1):
                    self._seen_width = width
                    self._sync_phone()
                return True

            self._phone_tick = self.add_tick_callback(tick)
        self._sync_phone()

    def _available_width(self) -> int:
        """The allocated container span, independent of our full/compact minimum.

        A sidebar can reduce this span without resizing the window. Walk out
        through wrappers because an overflowing child may keep its own minimum
        allocation even when an outer viewport has already become narrower.
        """
        widths = []
        child, parent = self, self.get_parent()
        while parent is not None:
            width = parent.get_width() - child.get_margin_start() - child.get_margin_end()
            if width > 0:
                widths.append(width)
            child, parent = parent, parent.get_parent()
        return min(widths) if widths else 0

    def _sync_phone(self) -> None:
        """Compact when the window is a phone's or too narrow for the whole bar (v70 `.win.phone .rlctl`)."""
        root = self.get_root()
        width = 0
        if root is not None:
            width = root.get_width()
            if width <= 0 and hasattr(root, "get_default_size"):
                width = root.get_default_size()[0]
        if getattr(self, "_full_width", 0) == 0 and not self.has_css_class("phone") and width > 0:
            self._full_width = self.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        full = getattr(self, "_full_width", 0)
        available = self._available_width() or width
        # Waveform decks change composition at the phone breakpoint even when
        # their desktop minimum fits; compacting an LCD bar is fit-driven.
        phone = 0 < width and (width <= tokens.PHONE_MAX_WIDTH if self.variant == "waveform"
                              else available < full if full else available <= tokens.PHONE_MAX_WIDTH)
        (self.add_css_class if phone else self.remove_css_class)("phone")
        if self.variant == "waveform":
            self._phone_shape(phone)
        for widget in getattr(self, "_phone_hidden", []):
            widget.set_visible(not phone)

    def _phone_shape(self, phone: bool) -> None:
        """Memos' deck under 560 (v71 `.medctl`): the transport centred on its own row, the time at the
        start and the speed at the end on the line below; the waveform's bars 1 px apart, 24 px keys."""
        keys, row = self._keys_row, self._controls
        if phone and keys.get_parent() is row:
            row.set_center_widget(None)
            self._column.prepend(keys)
        elif not phone and keys.get_parent() is self._column:
            self._column.remove(keys)
            row.set_center_widget(keys)
        for name, size in (("back", 24), ("forward", 24)):
            if name in self._keys:
                self._keys[name].glyph.set_size(size if phone else TR["icon"])
        self.play_key.glyph.set_size(28 if phone else TR["memo_play_icon"])
        if self.waveform is not None and hasattr(self.waveform, "set_gap"):
            self.waveform.set_gap(1 if phone else None)
        if self.waveform is not None and hasattr(self.waveform, "set_rail"):
            self.waveform.set_rail(not phone)

    # ── Intents ──
    def toggle_play(self) -> None:
        self.set_playing(not self._playing)
        if self.on_play is not None:
            self.on_play(self._playing)

    def _step(self, direction: int) -> None:
        if self.on_step is not None:
            self.on_step(direction)
        elif self._fps and self.variant == "attached":
            self._seek_to(self._position + direction / self._fps, user=True)

    def _skip(self, seconds: float) -> None:
        if self.on_skip is not None:
            self.on_skip(seconds)
        else:
            self._seek_to(self._position + seconds, user=True)

    def _seek_to(self, seconds: float, user: bool = False) -> None:
        seconds = min(max(0.0, float(seconds)), self._duration) if self._duration else max(0.0, float(seconds))
        self._show_position(seconds)
        if user and self.on_seek is not None:
            self.on_seek(seconds)

    def _volume_to(self, value: float, user: bool = False) -> None:
        self._volume = value
        if user and self.on_volume is not None:
            self.on_volume(value)

    def _flip_loop(self) -> None:
        self.set_loop(not self._loop)
        if self.on_loop is not None:
            self.on_loop(bool(self._loop))

    def _flip_shuffle(self) -> None:
        self.set_shuffle(not self._shuffle)
        if self.on_shuffle is not None:
            self.on_shuffle(bool(self._shuffle))

    def _flip_repeat(self) -> None:
        self.set_repeat(not self._repeat)
        if self.on_repeat is not None:
            self.on_repeat(bool(self._repeat))

    def _flip_record(self) -> None:
        self.set_recording(not self._recording)
        if self.on_record is not None:
            self.on_record(bool(self._recording))

    def _speed_rows(self) -> list:
        """The speed menu: the "Speed" heading, the rates, then (under a hairline) the app's own rows."""
        from .action_bubble import MenuItem

        def choose(rate: float) -> None:
            self.set_speed(rate)
            if self.on_speed is not None:
                self.on_speed(rate)

        rows: list = ["Speed"]
        rows += [MenuItem(speed_text(rate), selected=abs(rate - (self._speed or 1.0)) < 1e-6,
                          on_activate=lambda r=rate: choose(r)) for rate in self.speeds]
        extras = list(self.speed_extras()) if self.speed_extras is not None else []
        return rows + [None, *extras] if extras else rows

    def _speed_menu(self, button: Gtk.Button) -> None:
        from .action_bubble import FloatingMenu

        FloatingMenu(self._speed_rows(), label="Speed").popup(button, side="above")

    # ── State (the app's) ──
    @property
    def position(self) -> float:
        return self._position

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def loop(self) -> bool | None:
        return self._loop

    @property
    def shuffle(self) -> bool | None:
        return self._shuffle

    @property
    def repeat(self) -> bool | None:
        return self._repeat

    @property
    def speed(self) -> float | None:
        return self._speed

    @property
    def volume(self) -> float | None:
        return self._volume

    @property
    def scrubbing(self) -> bool:
        return self._scrubbing

    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, float(seconds)) if math.isfinite(float(seconds)) else 0.0
        if self.scrub is not None:
            self._updating = True
            self.scrub.set_range(0, max(0.001, self._duration))
            self.scrub.set_increments(5, 15)
            self.scrub.set_sensitive(self._duration > 0)
            self._updating = False
        self._show_position(min(self._position, self._duration) if self._duration else self._position)

    def set_position(self, seconds: float) -> None:
        """Where the player is; ignored while the listener holds the scrub."""
        if self._scrubbing or time.monotonic() < self._held_until:
            return
        self._show_position(seconds)

    def _show_position(self, seconds: float) -> None:
        seconds = float(seconds) if math.isfinite(float(seconds)) else 0.0
        self._position = max(0.0, min(seconds, self._duration) if self._duration else seconds)
        if self.scrub is not None:
            if self.scrub.get_adjustment().get_upper() != max(0.001, self._duration):
                self._updating = True
                self.scrub.set_range(0, max(0.001, self._duration))
                self.scrub.set_increments(5, 15)
                self._updating = False
            self._updating = True
            self.scrub.set_value(self._position)
            self._updating = False
            self.scrub.update_property([Gtk.AccessibleProperty.VALUE_TEXT],
                                       [f"{clock_text(self._position)} of {clock_text(self._duration)}"])
        if self.variant in ("deck", "now"):
            self._elapsed.set_label(clock_text(self._position))
            self._left.set_label(remaining_text(self._position, self._duration))
        elif self.variant == "waveform":
            self._elapsed.set_label(clock_text(self._position))
            self._total.set_label(f"/ {clock_text(self._duration)}")
            waveform = getattr(self, "waveform", None)
            if waveform is not None and hasattr(waveform, "set_position"):
                waveform.set_position(self._position)
        elif self.variant == "attached":
            self._set_cell(0, timecode_parts(self._position, self._fps))
            self._set_cell(1, (timecode_parts(self._duration, self._fps)[0], ""))

    def _set_cell(self, index: int, parts: tuple[str, str] | Sequence[str] | str) -> None:
        value, _caption = self._lcd_cells[index]
        child = value.get_first_child()
        while child is not None:
            value.remove(child)
            child = value.get_first_child()
        if isinstance(parts, str):
            value.append(Gtk.Label(label=parts))
            text = parts
        elif self.variant == "attached":
            main, dim = parts
            value.append(Gtk.Label(label=main))
            if dim:
                label = Gtk.Label(label=dim)
                label.add_css_class("dim")
                value.append(label)
            text = main + dim
        else:
            for position, part in enumerate(parts):
                if position:
                    dot = Gtk.Label(label=".")
                    dot.add_css_class("dim")
                    dot.add_css_class("dot")
                    value.append(dot)
                value.append(Gtk.Label(label=str(part)))
            text = ".".join(str(p) for p in parts)
        value.update_property([Gtk.AccessibleProperty.LABEL], [f"{_caption.get_label()}: {text}"])

    def set_lcd(self, readouts: Sequence[tuple]) -> None:
        """Session's LCD: `[(value, caption), …]`; a tuple value is joined by dimmed dots ("12.3.1")."""
        for index, (value, _caption) in enumerate(list(readouts)[:len(self._lcd_cells)]):
            self._set_cell(index, value if not isinstance(value, str) else str(value))

    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        self.play_key.glyph.set_name_("pause" if self._playing else "play")
        label = self._title("pause", "Pause") if self._playing else self._title("play", "Play")
        self.play_key.set_tooltip_text(label)
        self.play_key.update_property([Gtk.AccessibleProperty.LABEL], [label])
        (self.add_css_class if self._playing else self.remove_css_class)("playing")

    def set_loop(self, on: bool) -> None:
        self._loop = bool(on)
        self._sync_toggles()

    def set_shuffle(self, on: bool) -> None:
        self._shuffle = bool(on)
        self._sync_toggles()

    def set_repeat(self, on: bool) -> None:
        self._repeat = bool(on)
        self._sync_toggles()

    def set_recording(self, on: bool) -> None:
        self._recording = bool(on)
        self._sync_toggles()

    def set_volume(self, value: float | None) -> None:
        if value is None or not hasattr(self, "_volume_range"):
            return
        self._volume = min(1.0, max(0.0, float(value)))
        self._updating = True
        self._volume_range.set_value(self._volume)
        self._updating = False
        self._volume_range.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [f"{round(self._volume * 100)}%"])

    def set_speed(self, rate: float | None) -> None:
        if rate is None or not hasattr(self, "_speed_label"):
            return
        self._speed = float(rate)
        self._speed_label.set_label(speed_text(self._speed))
        self._keys["speed"].update_property([Gtk.AccessibleProperty.LABEL], [f"Speed {speed_text(self._speed)}"])

    def _sync_toggles(self) -> None:
        for key, value in (("loop", self._loop), ("shuffle", self._shuffle), ("repeat", self._repeat),
                           ("record", self._recording)):
            if key in self._keys and value is not None:
                _toggle(self._keys[key], bool(value))

    def key(self, name: str) -> Gtk.Button | None:
        """One of the transport's keys by name ("previous", "loop", "speed", …), for tests and focus."""
        return self.play_key if name == "play" else self._keys.get(name)

    # ── Keyboard ──
    def attach_shortcuts(self, widget: Gtk.Widget) -> Gtk.ShortcutController:
        """Space plays and pauses, Left/Right step, Home/End go to the ends, anywhere in `widget`.

        Text fields keep their own keys: the shortcuts listen after the focused field.
        """
        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)

        def add(trigger: str, action: Callable[[], None]) -> None:
            def run(*_args: object) -> bool:
                action()
                return True

            controller.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(trigger),
                                                     Gtk.CallbackAction.new(run)))

        add("space", self.toggle_play)
        if self.variant == "waveform":
            add("Left", lambda: self._skip(-15))
            add("Right", lambda: self._skip(15))
        else:
            add("Left", lambda: self._step(-1))
            add("Right", lambda: self._step(1))
        add("Home", lambda: self._seek_to(0.0, user=True))
        add("End", lambda: self._seek_to(self._duration, user=True))
        widget.add_controller(controller)
        return controller
